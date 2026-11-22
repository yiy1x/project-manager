import hashlib
import json

from sources import safe_text


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate_plan(plan, label="下一步", minimum_points=1):
    if not isinstance(plan, dict) or set(plan) != {"summary", "points"}:
        raise ValueError(label + "需要一句总结和短要点")
    summary = plan["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 40:
        raise ValueError(label + "总结应为1–40字")
    if not isinstance(plan["points"], list) or not minimum_points <= len(plan["points"]) <= 3:
        raise ValueError(label + f"需要{minimum_points}–3项要点")
    points = []
    for point in plan["points"]:
        if not isinstance(point, dict) or set(point) != {"label", "text"}:
            raise ValueError(label + "要点结构不合法")
        for key, limit in (("label", 4), ("text", 48)):
            if not isinstance(point[key], str) or not point[key].strip() or len(point[key]) > limit:
                raise ValueError(label + "要点过长或为空：" + key)
        points.append({key: safe_text(point[key].strip()) for key in ("label", "text")})
    return {"summary": safe_text(summary.strip()), "points": points}


def add_plans(root, tasks):
    for filename, field, output, label, minimum in (("next_steps.json", "next", "next_plan", "下一步", 1), ("confirmations.json", "blocker", "confirmation_plan", "待确认", 0)):
        path = root / filename
        presets = json.loads(path.read_text()).get("tasks", {}) if path.exists() else {}
        for task in tasks:
            plan = task.pop(output, None)
            preset = presets.get(task["id"], {})
            if plan is None and preset.get(field + "_hash") == text_hash(task.get(field, "")):
                plan = preset.get("plan")
            if plan is not None:
                try:
                    task[output] = validate_plan(plan, label, minimum)
                except ValueError:
                    pass
