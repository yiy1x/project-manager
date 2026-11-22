import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def read_segment(path, signature):
    messages = []
    turn = {}
    seen = set()
    with path.open() as stream:
        for line in stream:
            if not line.endswith("\n"):
                break
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("type") != "event_msg":
                continue
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "task_started":
                turn = {"turn_id": payload.get("turn_id"), "status": "inProgress", "started_at": payload.get("started_at"), "completed_at": None}
            elif payload.get("type") == "task_complete" and turn.get("turn_id") and payload.get("turn_id") == turn["turn_id"]:
                turn = {**turn, "status": "completed", "completed_at": payload.get("completed_at")}
            elif payload.get("type") == "turn_aborted" and turn.get("turn_id") and payload.get("turn_id") == turn["turn_id"]:
                turn = {**turn, "status": "interrupted"}
            if payload.get("type") != "item_completed":
                continue
            item = payload.get("item", {})
            if not isinstance(item, dict) or item.get("type") not in ("UserMessage", "AgentMessage", "userMessage", "agentMessage"):
                continue
            if item.get("id") in seen:
                continue
            if item.get("id"):
                seen.add(item["id"])
            content = item.get("content", [])
            text = item.get("text") or (content if isinstance(content, str) else "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and str(part.get("type", "text")).lower() in ("text", "input_text", "output_text")))
            if not isinstance(text, str) or not text.strip() or text.startswith("[external_agent_tool_") or text == "<EXTERNAL SESSION IMPORTED>":
                continue
            messages.append({"role": "user" if item["type"].lower() == "usermessage" else "assistant", "text": text})
    stat = path.stat()
    if (stat.st_ino, stat.st_mtime_ns, stat.st_size) != signature:
        raise ValueError("当前聊天记录正在变化，稍后重试")
    return messages, {key: value for key, value in turn.items() if key != "turn_id"}


def replacement_segment(path, identity, indexed_turn):
    if not path:
        return None
    path = Path(path)
    try:
        stat = path.stat()
        with path.open() as stream:
            header = json.loads(stream.readline())
    except FileNotFoundError:
        return None
    metadata = header.get("payload", {}) if isinstance(header, dict) else {}
    if not isinstance(metadata, dict) or header.get("type") != "session_meta" or metadata.get("id", metadata.get("session_id")) != identity:
        raise ValueError("当前聊天记录身份不匹配")
    created = datetime.fromisoformat(str(metadata.get("timestamp", header.get("timestamp"))).replace("Z", "+00:00")).timestamp()
    if indexed_turn and created <= (indexed_turn["started_at"] or 0):
        return None
    boundary = header.get("ordinal")
    if not isinstance(boundary, int) or boundary < 0:
        raise ValueError("当前聊天记录缺少有效序号")
    messages, turn = read_segment(path, (stat.st_ino, stat.st_mtime_ns, stat.st_size))
    return {"boundary": boundary, "messages": messages, "turn": turn}
