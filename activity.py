import json
import sqlite3
from datetime import datetime
from pathlib import Path

from sources import TIMEZONE, now, read_database, safe_text
from gateway import read_gateway
from codex_records import replacement_segment
from configuration import source_home


def timestamp(value):
    try:
        if isinstance(value, (int, float)):
            return value / 1000 if value > 100000000000 else value
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0


def status_record(raw, updated, basis, clock=None):
    clock = clock if clock is not None else datetime.now(TIMEZONE).timestamp()
    states = {
        "inProgress": ("running", "进行中"), "working": ("running", "进行中"),
        "idle": ("idle", "空闲"), "completed": ("idle", "本轮已结束"),
        "failed": ("error", "本轮出错"), "interrupted": ("paused", "已中断"),
    }
    kind, label = states.get(raw, ("unknown", "状态未知")) if isinstance(raw, str) else ("unknown", "状态未知")
    if (kind == "running" or raw == "idle") and not 0 <= clock - timestamp(updated) <= 120:
        kind, label = "unknown", "状态待确认"
    return {"kind": kind, "label": label, "updated_at": updated, "basis": basis, "raw": raw}


def event_time(value):
    instant = timestamp(value)
    if not 0 < instant <= datetime.now(TIMEZONE).timestamp() + 5:
        return None
    return datetime.fromtimestamp(instant, TIMEZONE).isoformat()


def read_activity(source_ids, home=None, gateway_snapshot=None):
    home = source_home(home)
    statuses = {}
    errors = []
    identities = [identity.removeprefix("codex:") for identity in source_ids if identity.startswith("codex:")]
    state = history = None
    if identities:
        try:
            state = read_database(home / ".codex/state_5.sqlite")
            history = read_database(home / ".codex/thread_history_1.sqlite")
            for identity in identities:
                row = state.execute("SELECT updated_at,rollout_path FROM threads WHERE id=?", (identity,)).fetchone()
                turn = history.execute("SELECT status, started_at, completed_at FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (identity,)).fetchone()
                basis = "Codex 本机保存轮次；不是进程心跳。进行中记录超过2分钟无更新则待确认；本轮结束不等于任务结项。"
                try:
                    replacement = replacement_segment(row["rollout_path"], identity, turn) if row else None
                    if replacement is not None:
                        turn = replacement["turn"]
                        basis = "Codex 当前续聊记录（旧索引落后）；不是进程心跳。本轮结束不等于任务结项。"
                except (OSError, ValueError, TypeError) as error:
                    turn = None
                    basis = "当前聊天记录读取失败，不能沿用旧轮次状态。"
                    errors.append("Codex 当前状态读取失败：" + safe_text(error))
                updated = datetime.fromtimestamp(row[0], TIMEZONE).isoformat() if row else None
                status = status_record(turn["status"] if turn else None, updated, basis)
                status["started_at"] = event_time(turn["started_at"]) if turn else None
                status["completed_at"] = event_time(turn["completed_at"]) if turn and turn["status"] in ("completed", "failed", "interrupted") else None
                statuses["codex:" + identity] = status
        except (OSError, sqlite3.Error, ValueError) as error:
            errors.append("Codex 状态读取失败：" + safe_text(error))
        finally:
            if state is not None:
                state.close()
            if history is not None:
                history.close()
    for path in (home / ".claude/sessions").glob("*.json"):
        try:
            record = json.loads(path.read_text())
            if not isinstance(record, dict) or not isinstance(record.get("sessionId"), str):
                continue
            updated = datetime.fromtimestamp(timestamp(record.get("updatedAt")), TIMEZONE).isoformat()
            status = status_record(record.get("status"), updated, "Claude Code 本机会话状态记录；不保证进程仍存活。无状态记录的会话显示未知，不以文件存在或HTTP成功推断运行。")
            status["started_at"] = event_time(record.get("startedAt"))
            status["completed_at"] = event_time(record.get("completedAt")) if record.get("status") in ("completed", "failed", "interrupted") else None
            statuses["claude:" + record["sessionId"]] = status
        except (OSError, ValueError, OverflowError) as error:
            errors.append("Claude 状态读取失败：" + safe_text(error))
    gateway = read_gateway() if gateway_snapshot is None else gateway_snapshot
    routes = [route for route in gateway["routes"] if route["agent"] == "claude" or route["source_id"] in source_ids]
    grouped = {}
    for route in routes:
        if route["source_id"]:
            grouped.setdefault(route["source_id"], []).append(route)
    for identity, requests in grouped.items():
        active = [request for request in requests if not request["done"]]
        latest = max(active or requests, key=lambda request: timestamp(request["time"]))
        previous = statuses.get(identity)
        current = datetime.now().timestamp()
        recent = 0 <= current - timestamp(latest["time"]) <= 120
        if not active and (not recent or (previous and (previous["kind"] == "running" or timestamp(previous["updated_at"]) > timestamp(latest["time"])) )):
            continue
        statuses[identity] = {
            "kind": latest["kind"], "label": latest["label"], "updated_at": gateway["observed_at"],
            "basis": "猫老大实时路由快照；按agent和session精确关联。请求结束不等于工具执行结束或任务结项。",
            "raw": "gateway", "active_requests": len(active),
            "started_at": event_time(latest["time"]) if active else (previous or {}).get("started_at"),
            "completed_at": (previous or {}).get("completed_at"),
        }
    return {"checked_at": now(), "statuses": statuses, "errors": errors, "gateway": {key: gateway[key] for key in ("connected", "observed_at", "checked_at", "error") if key in gateway}, "live_requests": [route for route in routes if route["agent"] == "claude" and not route["done"]]}
