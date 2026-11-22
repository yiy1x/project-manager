import json
import re
from datetime import date, datetime

from sources import TIMEZONE, now, safe_text

KINDS = ("想法", "调研", "决策", "实现", "验证", "阻塞")


def short_points(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not 1 <= len(lines) <= 3:
        return []
    points = []
    for line in lines:
        match = re.fullmatch(r"([^：:\n]{1,4})[：:]\s*(.+)", line)
        if not match or not match[1].strip() or not match[2].strip() or len(match[2]) > 48:
            return []
        points.append({"label": match[1].strip(), "text": match[2].strip()})
    return points


def manual_event(body, task_id):
    identity = body.get("id")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", identity):
        raise ValueError("节点标识不合法")
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError("请选择节点类型")
    fields = {}
    for key, label, limit, required in (("title", "标题", 80, True), ("detail", "变化说明", 1200, True), ("focus", "关注点", 40, False)):
        value = body.get(key, "")
        if not isinstance(value, str):
            raise ValueError(label + "必须是文字")
        value = safe_text(value).strip()
        if (required and not value) or len(value) > limit:
            raise ValueError(f"{label}应为{'1' if required else '0'}–{limit}字")
        fields[key] = value
    occurred_on = body.get("occurred_on")
    if not isinstance(occurred_on, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", occurred_on):
        raise ValueError("请选择有效的发生日期")
    if date.fromisoformat(occurred_on) > datetime.now(TIMEZONE).date():
        raise ValueError("时间线只记录已发生的变化，未来计划请写入提醒")
    return {"id": "manual:" + identity, "task_id": task_id, "kind": kind, **fields, "occurred_at": occurred_on, "precision": "day", "origin": "manual", "evidence": []}


def save_manual(connection, body, task_id):
    event = manual_event(body, task_id)
    existing = connection.execute("SELECT payload FROM milestones WHERE id=?", (event["id"],)).fetchone()
    if existing:
        previous = json.loads(existing[0])
        if any(previous.get(key) != value for key, value in event.items()):
            raise ValueError("节点标识已用于其他内容，请重新填写后保存")
        return
    event["recorded_at"] = now()
    connection.execute("INSERT INTO milestones(id,task_id,payload) VALUES (?,?,?)", (event["id"], task_id, json.dumps(event, ensure_ascii=False)))


def load_history(root, connection):
    path = root / "timeline.json"
    curated = json.loads(path.read_text())["events"] if path.exists() else []
    events = [{**event, "origin": "review"} for event in curated]
    events.extend(json.loads(row[0]) for row in connection.execute("SELECT payload FROM review_events"))
    events.extend(json.loads(row[0]) for row in connection.execute("SELECT payload FROM milestones"))
    for row in connection.execute("SELECT * FROM events ORDER BY id"):
        events.append({"id": f"queue:{row['id']}", "task_id": row["task_id"], "occurred_at": row["created_at"], "recorded_at": row["created_at"], "precision": "second", "kind": "管理", "focus": "跟进状态", "title": {"已完成": "用户确认结项", "跟进中": "恢复跟进", "暂不跟进": "暂不跟进"}.get(row["action"], row["action"]), "detail": f"总管队列变更为「{row['action']}」。这是管理操作，不替代成果验证，也不修改原聊天。", "points": [{"label": "队列", "text": "改为「" + row["action"] + "」。"}, {"label": "边界", "text": "只改跟进状态，不代表成果验收。"}], "origin": "queue", "evidence": []})
    return events


def for_task(events, task):
    identities = set()
    result = []
    for event in events:
        if event["task_id"] != task["id"]:
            continue
        if event["id"] in identities:
            raise ValueError("时间线存在重复节点标识：" + event["id"])
        identities.add(event["id"])
        event = {**event, "evidence": [reference for reference in event.get("evidence", []) if reference["source_id"] in task["source_ids"]]}
        if not event.get("points"):
            event["points"] = short_points(event["detail"])
        result.append(event)
    return sorted(result, key=lambda event: (event["occurred_at"][:10], event["occurred_at"][10:], event["recorded_at"], event["id"]), reverse=True)
