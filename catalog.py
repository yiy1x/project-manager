import json
import shlex
import threading
from datetime import datetime
from pathlib import Path

from sources import TIMEZONE, human_message, json_lines, now, safe_text, text_of
from configuration import source_home

CACHE = {}
LOCK = threading.Lock()


def read_session(path):
    signature = (path.stat().st_mtime_ns, path.stat().st_size)
    cached = CACHE.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]
    title = cwd = first = last = ""
    texts = []
    count = 0
    latest_time = 0
    for record in json_lines(path):
        if not isinstance(record, dict) or record.get("isSidechain"):
            continue
        if isinstance(record.get("cwd"), str):
            cwd = record["cwd"]
        if record.get("type") in ("custom-title", "ai-title"):
            title = record.get("customTitle") or record.get("aiTitle") or title
        if record.get("type") not in ("user", "assistant") or not isinstance(record.get("message"), dict):
            continue
        text = text_of(record["message"].get("content"))
        if not text.strip() or not human_message(text):
            continue
        count += 1
        try:
            latest_time = max(latest_time, datetime.fromisoformat(record.get("timestamp", "").replace("Z", "+00:00")).timestamp())
        except (AttributeError, ValueError, OverflowError):
            pass
        text = safe_text(text)
        texts.append(text)
        if record["type"] == "user":
            first = first or text
            last = text
    result = {
        "id": "claude:" + path.stem, "session_id": path.stem,
        "title": safe_text(title or (first.splitlines()[0][:90] if first else "无文字会话")),
        "cwd": safe_text(cwd), "project_id": path.parent.name,
        "updated": datetime.fromtimestamp(latest_time or path.stat().st_mtime, TIMEZONE).isoformat(),
        "time_basis": "最后对话" if latest_time else "文件修改时间（未记录对话时间）",
        "message_count": count, "preview": last[:280], "search_text": "\n".join(texts),
        "resume_command": ("cd -- " + shlex.quote(cwd) + " && " if cwd else "") + "claude --resume " + shlex.quote(path.stem),
    }
    CACHE[str(path)] = (signature, result)
    return result


def search_projects(query="", home=None):
    home = source_home(home)
    root = home / ".claude/projects"
    words = query.casefold().split()
    projects = {}
    errors = []
    total = 0
    with LOCK:
        paths = list(root.glob("*/*.jsonl"))
        present = {str(path) for path in paths}
        for key in list(CACHE):
            if str(root) in key and key not in present:
                del CACHE[key]
        for path in paths:
            if path.is_symlink() or path.parent.is_symlink():
                continue
            try:
                session = read_session(path)
            except (OSError, ValueError, TypeError, AttributeError) as error:
                errors.append(path.name + "：" + safe_text(error))
                continue
            if not session["message_count"]:
                continue
            total += 1
            identity = session["cwd"] or session["project_id"]
            group = projects.setdefault(identity, {"id": identity, "name": Path(session["cwd"]).name if session["cwd"] else session["project_id"], "path": session["cwd"], "session_count": 0, "sessions": []})
            group["session_count"] += 1
            searchable = "\n".join(str(session[key]) for key in ("title", "cwd", "project_id", "session_id", "search_text")).casefold()
            if all(word in searchable for word in words):
                visible = {key: value for key, value in session.items() if key != "search_text"}
                if words:
                    text = session["search_text"]
                    position = text.casefold().find(words[0])
                    if position >= 0:
                        visible["preview"] = text[max(0, position - 50):position + 230]
                group["sessions"].append(visible)
    matched = [group for group in projects.values() if group["sessions"]]
    for group in matched:
        group["sessions"].sort(key=lambda session: session["updated"], reverse=True)
    matched.sort(key=lambda group: group["sessions"][0]["updated"], reverse=True)
    return {"projects": matched, "project_count": len(projects), "session_count": total, "matched_count": sum(len(group["sessions"]) for group in matched), "checked_at": now(), "errors": errors, "available": root.exists()}
