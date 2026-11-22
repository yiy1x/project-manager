from sources import now


def analysis():
    return {
        "analysis_date": "演示数据",
        "analysis_method": "全部内容为虚构示例，不扫描本机，不连接猫老大，不调用模型。",
        "highlights": [],
        "tasks": [
            {
                "id": "demo-dashboard", "project": "示例项目", "name": "给阅读清单加上项目看板",
                "priority": "P1", "stage": "看板已实现，筛选待验收", "confidence": "虚构演示，不代表真实工作进度",
                "source_ids": ["codex:demo-dashboard"], "goal": "把零散的阅读记录整理成可筛选、可回顾的项目看板。",
                "progress": "已完成列表和详情视图。正在检查结项后的计数与筛选是否一致。",
                "blocker": "需要验收筛选结果；演示不会实际调用模型。", "next": "先检查状态筛选，再补充使用说明。",
                "next_plan": {"summary": "先验收筛选，再补充使用说明", "points": [{"label": "验收", "text": "结项一条任务，核对左侧数量与列表。"}, {"label": "文档", "text": "记录如何添加聊天和查看阶段变化。"}]},
                "confirmation_plan": {"summary": "筛选结果还需要验收", "points": [{"label": "范围", "text": "当前为虚构示例，不代表真实项目已交付。"}]},
                "evidence": ["此任务及时间线均为独立编写的演示内容。"], "cautions": ["不包含任何用户聊天或项目路径。"],
            },
            {
                "id": "demo-notes", "project": "写作示例", "name": "整理一份入门指南",
                "priority": "P2", "stage": "提纲已确认，正文待写", "confidence": "虚构演示",
                "source_ids": ["claude:demo-notes"], "goal": "写清项目的用途、使用方式和隐私边界。",
                "progress": "已确定三个章节，尚未开始正文。", "blocker": "暂无明确待确认事项。", "next": "从本地启动流程开始写作。",
                "next_plan": {"summary": "先写本地启动流程", "points": [{"label": "写作", "text": "说明依赖、启动命令和数据保存位置。"}]},
                "confirmation_plan": {"summary": "暂无明确待确认事项", "points": []}, "evidence": ["虚构示例。"], "cautions": [],
            },
        ],
    }


def history():
    events = []
    for identity, timestamp, kind, summary, detail, points in (
        ("demo-scope", "2026-11-10", "决策", "确定看板范围，先做列表和详情", "示例：先管理任务状态，再记录阶段变化。", [{"label": "范围", "text": "先做任务列表、详情和状态筛选。"}]),
        ("demo-build", "2026-11-12", "实现", "完成列表和详情，接下来验收筛选", "示例：列表和详情已实现，筛选仍待验收。", [{"label": "完成", "text": "任务列表和详情可以一起查看。"}, {"label": "待验", "text": "结项后的数量和筛选仍需检查。"}]),
    ):
        events.append({"id": identity, "task_id": "demo-dashboard", "occurred_at": timestamp, "recorded_at": timestamp, "precision": "day", "kind": kind, "focus": "示例看板", "title": summary, "summary": summary, "detail": detail, "points": points, "evidence": []})
    return {"events": events}


def snapshot():
    entries = [{"id": task["source_ids"][0], "native_id": task["id"], "source": "Codex" if task["id"] == "demo-dashboard" else "Claude Code", "title": task["name"], "category": "聊天", "fingerprint": "demo", "message_count": 2, "updated": "2026-11-12T10:00:00+08:00", "latest_request": task["next"], "latest_response": task["progress"], "saved_turn_status": {"status": "completed"}, "route_evidence": "虚构示例，未连接真实路由", "aliases": [], "coverage": "匿名演示", "runtime": "演示状态"} for task in analysis()["tasks"]]
    return {"scanned_at": now(), "entries": entries, "statistics": {"codex_threads": 1, "claude_files": 1, "import_duplicates": 0, "gateway_files": 0, "route_requests": 0, "route_dates": []}, "errors": [], "scope": "演示模式：全部内容虚构，不读取本机聊天、不连接猫老大、不调用模型。"}


def activity():
    return {"checked_at": now(), "statuses": {}, "errors": [], "live_requests": [], "gateway": {"connected": False, "checked_at": now(), "error": "演示模式，未连接猫老大"}}


def catalog():
    return {"projects": [], "project_count": 0, "session_count": 0, "matched_count": 0, "checked_at": now(), "errors": [], "available": False}
