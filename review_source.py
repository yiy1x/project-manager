import hashlib
import json
from contextlib import closing
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from configuration import source_home

from sources import TIMEZONE, human_message, read_database, safe_text, text_of


def fingerprint(messages):
    dialogue = [{"role": message["role"], "text": message["text"]} for message in messages]
    return hashlib.sha256(json.dumps(dialogue, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def message_text(item):
    content = item.get("content", [])
    return item.get("text") or (content if isinstance(content, str) else "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and str(part.get("type", "text")).lower() in ("text", "input_text", "output_text")))


def date_text(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, TIMEZONE).isoformat(timespec="seconds")
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TIMEZONE).isoformat(timespec="seconds")
    return ""


@lru_cache(maxsize=16)
def read_tail(path, offset, signature):
    messages = []
    turn = {}
    last_completed = {}
    seen = set()
    with path.open("rb") as stream:
        stream.seek(offset)
        for line in stream:
            if not line.endswith(b"\n"):
                break
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("type") != "event_msg":
                continue
            payload = record.get("payload", {})
            kind = payload.get("type")
            if kind == "task_started":
                turn = {"turn_id": payload.get("turn_id"), "status": "inProgress", "started_at": payload.get("started_at"), "completed_at": None}
            elif kind == "task_complete" and turn.get("turn_id") and payload.get("turn_id") == turn["turn_id"]:
                turn.update(status="completed", completed_at=payload.get("completed_at"))
                if turn["completed_at"] and any(message["turn_id"] == turn["turn_id"] and message["phase"] == "final_answer" for message in messages):
                    last_completed = {"turn_key": turn["turn_id"], "count": len(messages), "completed_at": turn["completed_at"]}
            elif kind == "turn_aborted" and turn.get("turn_id") and payload.get("turn_id") == turn["turn_id"]:
                turn["status"] = "interrupted"
            if kind != "item_completed":
                continue
            item = payload.get("item", {})
            if item.get("type") not in ("UserMessage", "AgentMessage", "userMessage", "agentMessage") or item.get("id") in seen:
                continue
            seen.add(item.get("id"))
            text = message_text(item)
            if not isinstance(text, str) or not text.strip() or text.startswith("[external_agent_tool_") or text == "<EXTERNAL SESSION IMPORTED>":
                continue
            ordinal = record.get("ordinal")
            if type(ordinal) is not int:
                raise ValueError("聊天事件缺少消息序号")
            role = "user" if item["type"].lower() == "usermessage" else "assistant"
            if role == "user" and turn.get("status") == "completed":
                turn = {"turn_id": None, "status": "inProgress", "completed_at": None}
            messages.append({"ordinal": ordinal, "message_id": item.get("id", str(ordinal)), "role": role, "text": text, "phase": item.get("phase"), "occurred_at": date_text(record.get("timestamp")), "turn_id": turn.get("turn_id"), "record_path": str(path)})
    stat = path.stat()
    if (stat.st_ino, stat.st_mtime_ns, stat.st_size) != signature:
        raise ValueError("聊天记录正在变化，稍后重新读取")
    return messages, {**turn, "last_completed": last_completed}


def read_source(identity, home=None):
    if not identity.startswith("codex:"):
        raise ValueError("当前自动分析需要Codex的整轮完成记录")
    home = source_home(home)
    native_id = identity.split(":", 1)[1]
    with closing(read_database(home / ".codex/state_5.sqlite")) as state, closing(read_database(home / ".codex/thread_history_1.sqlite")) as history:
        state.execute("BEGIN")
        history.execute("BEGIN")
        source = state.execute("SELECT rollout_path FROM threads WHERE id=?", (native_id,)).fetchone()
        indexed = history.execute("SELECT * FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (native_id,)).fetchone()
        if not source or not indexed:
            raise ValueError("未找到可确认的聊天轮次")
        indexed = dict(indexed)
        path = Path(source["rollout_path"])
        stat = path.stat()
        with path.open() as stream:
            header = json.loads(stream.readline())
        metadata = header.get("payload", {})
        if header.get("type") != "session_meta" or metadata.get("id", metadata.get("session_id")) != native_id:
            raise ValueError("聊天文件身份不匹配")
        created = datetime.fromisoformat(str(metadata.get("timestamp", header.get("timestamp"))).replace("Z", "+00:00")).timestamp()
        replacement = created > (indexed.get("started_at") or 0)
        completed_index = history.execute("SELECT * FROM thread_turns WHERE thread_id=? AND status='completed' ORDER BY rollout_ordinal DESC LIMIT 1", (native_id,)).fetchone()
        tail_start = dict(completed_index) if completed_index and not replacement else indexed
        boundary = header.get("ordinal") if replacement else tail_start["rollout_ordinal"]
        offset = 0 if replacement else tail_start.get("rollout_byte_offset", 0)
        if type(boundary) is not int or type(offset) is not int or boundary < 0 or not 0 <= offset <= stat.st_size:
            raise ValueError("聊天轮次位置不合法")
        tail, actual = read_tail(path, offset, (stat.st_ino, stat.st_mtime_ns, stat.st_size))
        messages = []
        for row in history.execute("SELECT * FROM thread_items WHERE thread_id=? AND item_type IN ('userMessage','agentMessage') ORDER BY rollout_ordinal", (native_id,)):
            if row["rollout_ordinal"] >= boundary:
                continue
            item = json.loads(row["item_json"])
            text = item.get("text") or text_of(item.get("content"))
            if text.startswith("[external_agent_tool_") or text == "<EXTERNAL SESSION IMPORTED>":
                continue
            messages.append({"ordinal": row["rollout_ordinal"], "message_id": item.get("id", row["item_id"]), "role": "user" if item["type"] == "userMessage" else "assistant", "text": text, "phase": item.get("phase"), "occurred_at": date_text(row["created_at_ms"]), "turn_id": row["turn_id"], "record_path": ""})
        prefix_count = len(messages)
        messages.extend(tail)
        if not actual or not actual.get("turn_id"):
            if actual.get("status") == "inProgress":
                turn = actual
            elif replacement:
                raise ValueError("当前续聊缺少可确认的轮次事件")
            else:
                turn = indexed
        else:
            turn = actual
        final = [message for message in messages if message["role"] == "assistant" and message["phase"] == "final_answer" and message["turn_id"] == turn.get("turn_id")]
        complete = turn.get("status") == "completed" and bool(turn.get("completed_at")) and bool(final)
        source = {"source_id": identity, "messages": messages, "fingerprint": fingerprint(messages), "turn_key": turn.get("turn_id") or "", "status": turn.get("status", "unknown"), "completed": complete, "completed_at": date_text(turn.get("completed_at")), "record_path": str(path)}
        previous = actual.get("last_completed")
        if not complete and previous:
            reviewed = messages[:prefix_count + previous["count"]]
            source["completed_prefix"] = {**source, "messages": reviewed, "fingerprint": fingerprint(reviewed), "turn_key": previous["turn_key"], "status": "completed", "completed": True, "completed_at": date_text(previous["completed_at"])}
        final_stat = path.stat()
        if (stat.st_ino, stat.st_mtime_ns, stat.st_size) != (final_stat.st_ino, final_stat.st_mtime_ns, final_stat.st_size):
            raise ValueError("聊天记录正在变化，稍后重新读取")
        return source


def model_messages(messages):
    result = []
    for message in messages:
        text = message["text"]
        if not human_message(text) or text.lstrip().startswith(("<external_browser_tabs>", "<system_reminder>", "<user_instructions>")):
            continue
        result.append({"id": message["message_id"], "role": message["role"], "time": message["occurred_at"], "phase": message["phase"], "text": safe_text(text)})
    return result
