import collections
import gzip
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from codex_records import replacement_segment
from configuration import source_home

TIMEZONE = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


def safe_text(value):
    value = str(value or "")
    value = re.sub(r"\b(?:sk-|sk_)[A-Za-z0-9_-]{16,}", "[已隐藏密钥]", value)
    value = re.sub(r"(?i)((?:api[_ -]?key|authorization|auth[_ -]?token|appid|password)\s*[\"']?\s*[:=]\s*)([^\s,;}]+)", r"\1[已隐藏凭据]", value)
    return value


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content or [] if isinstance(part, dict) and part.get("type", "text") in ("text", "input_text", "output_text"))


def json_lines(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def read_database(path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def human_message(text):
    return bool(text.strip()) and not text.lstrip().startswith(("<environment_context>", "<local-command", "<command-name>", "<task-notification>", "This session is being continued", "[Request interrupted", "[Your previous response", "# Workflow authoring reference"))


def add_dialogue(entry, messages):
    direct = [message for message in messages if human_message(message["text"])]
    user = [message for message in direct if message["role"] == "user"]
    assistant = [message for message in direct if message["role"] == "assistant"]
    entry["message_count"] = len(messages)
    entry["latest_request"] = safe_text(user[-1]["text"][:1600]) if user else ""
    entry["latest_response"] = safe_text(assistant[-1]["text"][:2200]) if assistant else ""
    entry["recent_requests"] = [safe_text(message["text"][:260]) for message in user[-6:]]
    entry["fingerprint"] = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if not entry.get("title"):
        entry["title"] = safe_text(user[0]["text"].splitlines()[0][:90]) if user else "无正文会话"


def collect(home=None, allowed_ids=None):
    entries = {}
    errors = []
    statistics = {"codex_threads": 0, "claude_files": 0, "import_duplicates": 0, "gateway_files": 0}
    if allowed_ids is not None and not allowed_ids:
        return {"scanned_at": now(), "entries": [], "statistics": {**statistics, "route_requests": 0, "route_dates": [], "inventory_entries": 0, "chat_entries": 0}, "errors": [], "scope": "尚未添加聊天。先运行 python3 app.py discover，再用 track 加入指定来源；不会自动导入全部聊天。"}
    home = source_home(home)
    route_groups = {}
    routes = []
    for path in sorted((home / ".config/catboss/routing").glob("*.jsonl*")):
        try:
            routes.extend(route for route in json_lines(path) if allowed_ids is None or f"{route.get('agent')}:{route.get('session')}" in allowed_ids)
        except (OSError, EOFError) as error:
            errors.append(f"路由记录读取失败：{path.name}：{error}")
    for route in routes:
        identity = (route.get("agent", "unknown"), route.get("session"))
        route_groups.setdefault(identity, []).append(route)
    statistics["route_requests"] = len(routes)
    statistics["route_dates"] = sorted({str(route.get("time", ""))[:10] for route in routes})
    import_path = home / ".codex/external_agent_session_imports.json"
    imported = {}
    try:
        imports = json.loads(import_path.read_text()).get("records", []) if import_path.exists() else []
        if not isinstance(imports, list):
            raise ValueError("导入映射 records 必须是列表")
        for record in imports:
            if not isinstance(record, dict) or not isinstance(record.get("source_path"), str) or not isinstance(record.get("imported_thread_id"), str):
                raise ValueError("导入映射缺少来源路径或线程 ID")
            imported[Path(record["source_path"]).stem] = record["imported_thread_id"]
    except (OSError, ValueError, AttributeError) as error:
        errors.append(f"导入映射读取失败：{error}")
    if allowed_ids is not None:
        imported = {key: value for key, value in imported.items() if "codex:" + value in allowed_ids}
    imported_reverse = {value: key for key, value in imported.items()}
    state = None
    history = None
    try:
        state = read_database(home / ".codex/state_5.sqlite")
        history = read_database(home / ".codex/thread_history_1.sqlite")
        rows = state.execute("SELECT id, name, title, cwd, source, model_provider, updated_at, archived, rollout_path FROM threads WHERE model_provider='catboss'").fetchall()
        if allowed_ids is not None:
            rows = [row for row in rows if "codex:" + row["id"] in allowed_ids]
        statistics["codex_threads"] = len(rows)
        for row in rows:
            identity = "codex:" + row["id"]
            internal = '"subagent"' in row["source"] or row["source"] == "guardian"
            entry = {"id": identity, "native_id": row["id"], "source": "Codex", "title": row["name"] or row["title"], "cwd": row["cwd"], "category": "系统/子代理" if internal else "聊天", "updated": datetime.fromtimestamp(row["updated_at"], TIMEZONE).isoformat(), "archived": bool(row["archived"]), "path": row["rollout_path"], "link": f"codex://threads/{row['id']}", "route_evidence": "索引声明 catboss；历史调用未全部留存", "aliases": [], "coverage": "本机保存的用户与助手文字；不含完整工具输出"}
            if row["id"] in imported_reverse:
                entry["aliases"] = ["claude:" + imported_reverse[row["id"]]]
                entry["route_evidence"] = "Claude 历史导入副本，不能据此证明原调用走猫老大"
            messages = []
            turn = history.execute("SELECT status,started_at,completed_at FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (row["id"],)).fetchone()
            replacement = None
            if not internal:
                try:
                    replacement = replacement_segment(row["rollout_path"], row["id"], turn)
                except (OSError, ValueError, TypeError) as error:
                    errors.append(f"Codex 当前记录读取失败：{identity}：{safe_text(error)}")
                    entry["source_stale"] = True
                    turn = None
            if not internal:
                for message_row in history.execute("SELECT item_json,rollout_ordinal FROM thread_items WHERE thread_id=? AND item_type IN ('userMessage','agentMessage') ORDER BY rollout_ordinal", (row["id"],)):
                    if replacement is not None and message_row["rollout_ordinal"] >= replacement["boundary"]:
                        continue
                    message = json.loads(message_row["item_json"])
                    text = message.get("text") or text_of(message.get("content"))
                    if text.startswith("[external_agent_tool_") or text == "<EXTERNAL SESSION IMPORTED>":
                        continue
                    messages.append({"role": "user" if message["type"] == "userMessage" else "assistant", "text": text})
            if replacement is not None:
                messages.extend(replacement["messages"])
                turn = replacement["turn"]
                entry["coverage"] = "本机历史前缀＋当前续聊记录；旧索引落后，按当前记录替换重叠段；不含完整工具输出"
            entry["saved_turn_status"] = dict(turn) if turn else {}
            entry["runtime"] = "未核验即时状态；保存轮次：" + (turn["status"] if turn else "未知")
            add_dialogue(entry, messages)
            entries[identity] = entry
    except (OSError, sqlite3.Error, ValueError) as error:
        errors.append(f"Codex 读取失败：{error}")
    finally:
        if state:
            state.close()
        if history:
            history.close()
    claude_paths = list((home / ".claude/projects").glob("*/*.jsonl"))
    if allowed_ids is not None:
        claude_paths = [path for path in claude_paths if "claude:" + path.stem in allowed_ids or path.stem in imported]
    statistics["claude_files"] = len(claude_paths)
    statistics["import_duplicates"] = len(imported)
    for path in claude_paths:
        identity = "codex:" + imported[path.stem] if path.stem in imported else "claude:" + path.stem
        messages = []
        entry = entries.get(identity, {"id": identity, "native_id": path.stem, "source": "Claude Code", "category": "聊天", "title": None, "cwd": "", "aliases": [], "route_evidence": "历史关联记录，路由未证实", "coverage": "本机保存的用户与助手文字；不含完整工具输出"})
        try:
            for record in json_lines(path):
                entry["cwd"] = record.get("cwd", entry["cwd"])
                if record.get("type") in ("ai-title", "custom-title") and not identity.startswith("codex:"):
                    entry["title"] = record.get("customTitle") or record.get("aiTitle") or entry.get("title")
                if record.get("type") not in ("user", "assistant"):
                    continue
                text = text_of(record.get("message", {}).get("content"))
                if text.strip():
                    messages.append({"role": record["type"], "text": text})
                    entry["updated"] = record.get("timestamp") or entry.get("updated")
            entry["original_path"] = str(path)
            if not identity.startswith("codex:"):
                entry["path"] = str(path)
                entry["runtime"] = "仅有保存记录，不代表进程正在运行"
            add_dialogue(entry, messages)
            if not messages:
                entry["category"] = "仅标题"
            entries[identity] = entry
        except OSError as error:
            errors.append(f"Claude 读取失败：{path.stem}：{error}")
    gateway_paths = list((home / ".config/catboss/gateway-conversations").glob("*/*/turns.jsonl"))
    if allowed_ids is not None:
        gateway_paths = []
    statistics["gateway_files"] = len(gateway_paths)
    for path in gateway_paths:
        try:
            turns = list(json_lines(path))
            if not turns:
                continue
            last = turns[-1]
            session = last.get("session")
            source = last.get("agent")
            identity = f"{source}:{session}"
            if identity not in entries:
                entry = {"id": identity, "native_id": session, "source": source, "title": None, "cwd": "", "category": "网关记录", "path": str(path), "aliases": [], "coverage": "网关片段，可能截断；不是完整聊天", "runtime": "HTTP 请求结果不是任务进度"}
                messages = []
                seen = set()
                for turn in turns:
                    for message in turn.get("input", []) + turn.get("output", []):
                        text = message.get("text", "")
                        key = (message.get("role"), text)
                        if message.get("kind") != "text" or message.get("role") not in ("user", "assistant") or key in seen:
                            continue
                        seen.add(key)
                        messages.append({"role": message["role"], "text": text})
                add_dialogue(entry, messages)
                entries[identity] = entry
            entries[identity]["gateway"] = {"records": len(turns), "last": last.get("time"), "status": last.get("status"), "cut": any(turn.get("cut") for turn in turns)}
        except OSError as error:
            errors.append(f"网关读取失败：{path.parent.name}：{error}")
    for (agent, session), group in route_groups.items():
        identity = f"{agent}:{session}" if session else f"route:{agent}"
        if agent == "claude" and session in imported:
            identity = "codex:" + imported[session]
        if identity not in entries:
            is_title = all(route.get("kind") == "thread_title" for route in group)
            entries[identity] = {"id": identity, "native_id": session, "source": agent, "title": "自动标题请求" if is_title else f"{agent} 未绑定会话的调用", "category": "标题生成" if is_title else "未绑定请求", "cwd": "", "message_count": 0, "aliases": [], "coverage": "仅路由元数据，无可确认的聊天正文", "runtime": "不能推断任务状态", "fingerprint": ""}
        entry = entries[identity]
        entry["route_evidence"] = "猫老大路由日志已证实"
        entry["route"] = {"requests": len(group), "first": group[0].get("time"), "last": group[-1].get("time"), "statuses": dict(collections.Counter(str(route.get("status")) for route in group)), "last_status": group[-1].get("status")}
        entry.setdefault("updated", group[-1].get("time"))
    statistics["inventory_entries"] = len(entries)
    statistics["chat_entries"] = sum(entry["category"] == "聊天" for entry in entries.values())
    scope = "本机猫老大留存路由 + Codex + Claude 原始历史；ChatGPT 账号历史入口不可用；OpenClaw 无 session 的请求仅登记，不强行关联正文。"
    if allowed_ids is not None:
        scope = f"固定跟进用户指定的 {len(allowed_ids)} 条聊天；其他聊天、系统记录和网关正文片段不再导入。只读原聊天，不删除原始记录。"
    return {"scanned_at": now(), "entries": list(entries.values()), "statistics": statistics, "errors": errors, "scope": scope}
