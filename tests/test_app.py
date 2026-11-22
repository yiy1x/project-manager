import copy
import json
import queue
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import sources


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="manager-regression-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = {
            "scanned_at": "2026-11-11T19:00:00+08:00", "scope": "隔离测试", "errors": [],
            "statistics": {"codex_threads": 4, "claude_files": 0, "import_duplicates": 0, "gateway_files": 0, "route_requests": 0, "route_dates": []},
            "entries": [{"id": "codex:" + identity, "title": identity, "category": "聊天", "fingerprint": "original", "source": "Codex"} for identity in ("environment", "pet", "manager", "new")],
        }
        tasks = [{"id": identity, "name": identity, "project": project, "source_ids": ["codex:" + identity], "priority": "P1", "stage": "等待验收", "confidence": "测试", "goal": "目标", "progress": "进度", "blocker": "阻塞", "next": "下一步", "evidence": [], "cautions": []} for identity, project in (("environment", "开发环境"), ("pet", "桌面体验"), ("manager", "项目总管"))]
        self.write_json("analysis.json", {"tasks": tasks, "analysis_date": "2026-11-11", "analysis_method": "隔离测试", "highlights": []})
        self.write_json("tracking.json", {"mode": "allowlist", "source_ids": [entry["id"] for entry in self.snapshot["entries"]]})
        for filename in ("index.html", "ui.js", "view-state.js", "style.css"):
            shutil.copyfile(ROOT / filename, self.root / filename)
        for name, value in {"ROOT": self.root, "DATABASE": self.root / "test.sqlite3", "LAST_SCAN": 0.0, "TOKEN": "test-token", "collect": lambda **options: copy.deepcopy(self.snapshot)}.items():
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app.initialize()
        app.refresh(True)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def write_json(self, filename, data):
        (self.root / filename).write_text(json.dumps(data, ensure_ascii=False))

    def request(self, path, body=None, headers=None, raw=None):
        request_headers = {"Content-Type": "application/json", "X-Manager-Token": app.TOKEN, **(headers or {})}
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        try:
            response = urlopen(Request(self.base + path, data=payload, headers=request_headers), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            result = json.loads(content) if "application/json" in response.headers["Content-Type"] else content.decode()
            return response.status, result, response.headers

    def post(self, path, body):
        status, result, _ = self.request(path, body)
        self.assertEqual(status, 200, result)
        return result

    def data(self):
        status, result, _ = self.request("/api/state")
        self.assertEqual(status, 200, result)
        return result

    def task(self, identity="pet"):
        return next(task for task in self.data()["tasks"] if task["id"] == identity)

    def test_all_assets_and_request_protection(self):
        for path in ("/", "/ui.js", "/view-state.js", "/style.css", "/api/state"):
            status, _, headers = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers["Cache-Control"], "no-store")
        html = self.request("/")[1]
        self.assertLess(html.index("/view-state.js"), html.index("/ui.js"))
        self.assertEqual(self.request("/analysis.json")[0], 404)
        self.assertEqual(self.request("/api/state", headers={"Host": "attacker.example"})[0], 403)
        for headers in ({"X-Manager-Token": "wrong"}, {"Origin": "http://attacker.example"}):
            self.assertEqual(self.request("/api/queue", {"task_id": "pet", "queue": "已完成"}, headers=headers)[0], 403)
        self.assertEqual(self.task()["queue"], "跟进中")

    def test_completion_refresh_restart_restore_and_pause(self):
        self.post("/api/notes", {"task_id": "pet", "text": "保留提醒"})
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        self.snapshot["entries"][1].update(title="重命名", fingerprint="new-content")
        self.post("/api/refresh", {})
        app.LAST_SCAN = 0
        app.initialize()
        task = self.task()
        self.assertEqual(task["queue"], "已完成")
        self.assertEqual(task["sources"][0]["title"], "重命名")
        self.assertEqual(task["notes"][0]["text"], "保留提醒")
        self.assertTrue(task["needs_review"])
        self.assertEqual(sum(task["queue"] == "跟进中" for task in self.data()["tasks"]), 2)
        for queue in ("跟进中", "暂不跟进", "跟进中"):
            self.post("/api/queue", {"task_id": "pet", "queue": queue})
            self.assertEqual(self.task()["queue"], queue)
        self.assertTrue(self.task()["needs_review"])

    def test_duplicate_completion_is_idempotent(self):
        self.post("/api/queue", {"task_id": "pet", "queue": "跟进中"})
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: self.post("/api/queue", {"task_id": "pet", "queue": "已完成"}), range(8)))
        with app.database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(self.task()["queue"], "已完成")

    def test_auto_review_reports_success_time_separately_from_scan_and_manual_baseline(self):
        self.assertEqual(self.data()["auto_review"]["last_success_at"], "")
        with app.database() as connection:
            connection.execute("INSERT INTO review_jobs(id,task_id,source_id,turn_key,status,available_at,created_at,finished_at,payload) VALUES ('success','environment','codex:environment','turn','completed',0,'2026-11-16T10:44:00+08:00','2026-11-16T10:45:25+08:00','{}')")
            connection.execute("INSERT INTO review_jobs(id,task_id,source_id,turn_key,status,available_at,created_at,finished_at,payload) VALUES ('failure','environment','codex:environment','next','failed',0,'2026-11-16T11:00:00+08:00','2026-11-16T11:01:00+08:00','{}')")
        reviewer = MagicMock(error="", last_checked_at="2026-11-16T11:02:00+08:00")
        with patch.object(app, "REVIEWER", reviewer):
            data = self.data()
        self.assertEqual(data["analysis_date"], "2026-11-11")
        self.assertEqual(data["auto_review"]["last_success_at"], "2026-11-16T10:45:25+08:00")
        self.assertEqual(data["auto_review"]["checked_at"], "2026-11-16T11:02:00+08:00")
        app.export_report(data)
        report = (self.root / "盘点报告.md").read_text()
        self.assertIn("人工分析基线：2026-11-11", report)
        self.assertIn("最近自动回读成功：2026-11-16T10:45:25+08:00", report)
        self.assertNotIn("语义分析日期：", report)

    def test_auto_review_toggle_is_authorized_and_preserves_user_state(self):
        reviewer = MagicMock()
        reviewer.error = ""
        reviewer.last_checked_at = ""
        with patch.object(app, "REVIEWER", reviewer):
            for payload in ({}, {"enabled": 1}, {"enabled": "true"}):
                self.assertEqual(self.request("/api/auto-review", payload)[0], 400)
            self.assertFalse(self.data()["auto_review"]["enabled"])
            self.assertEqual(self.request("/api/auto-review", {"enabled": True}, headers={"X-Manager-Token": "wrong"})[0], 403)
            self.post("/api/notes", {"task_id": "pet", "text": "保留提醒"})
            self.post("/api/auto-review", {"enabled": True})
            self.assertTrue(self.data()["auto_review"]["enabled"])
            self.post("/api/auto-review", {"enabled": False})
            self.assertFalse(self.data()["auto_review"]["enabled"])
            reviewer.runner.cancel.assert_called_once()
            self.assertEqual(self.task()["notes"][0]["text"], "保留提醒")
            self.assertEqual(self.task()["queue"], "跟进中")

    def test_auto_review_retry_only_changes_eligible_failed_jobs(self):
        self.post("/api/auto-review", {"enabled": True})
        with app.database() as connection:
            connection.execute("INSERT INTO review_jobs(id,task_id,source_id,turn_key,status,attempts,available_at,created_at,error,payload) VALUES ('failed','pet','codex:pet','turn','failed',3,0,'2026-11-13','error','{}')")
        self.assertEqual(self.task()["auto_analysis"]["state"], "failed")
        self.post("/api/review-retry", {"task_id": "pet"})
        with app.database() as connection:
            row = connection.execute("SELECT status,attempts,error FROM review_jobs").fetchone()
            self.assertEqual(tuple(row), ("queued", 0, None))
        self.post("/api/queue", {"task_id": "pet", "queue": "暂不跟进"})
        self.assertEqual(self.request("/api/review-retry", {"task_id": "pet"})[0], 400)
        self.assertEqual(self.request("/api/review-retry", {"task_id": "manager"})[0], 400)
        self.post("/api/queue", {"task_id": "pet", "queue": "跟进中"})
        self.post("/api/auto-review", {"enabled": False})
        self.assertEqual(self.request("/api/review-retry", {"task_id": "pet"})[0], 400)

    def test_queue_and_event_are_atomic(self):
        with app.database() as connection:
            connection.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertEqual(self.request("/api/queue", {"task_id": "pet", "queue": "已完成"})[0], 400)
        self.assertEqual(self.task()["queue"], "跟进中")
        with app.database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_invalid_payloads_never_change_state(self):
        for raw in (b"null", b"[]", b"42", b'"text"', b"{", b"\xff"):
            self.assertEqual(self.request("/api/queue", raw=raw)[0], 400)
        for body in ({"task_id": [], "queue": "已完成"}, {"task_id": "missing", "queue": "已完成"}, {"task_id": "pet", "queue": {}}, {"task_id": "pet", "queue": "unknown"}):
            self.assertEqual(self.request("/api/queue", body)[0], 400)
        for text in (None, {}, " ", "长" * 3001):
            self.assertEqual(self.request("/api/notes", {"task_id": "pet", "text": text})[0], 400)
        self.assertEqual(self.request("/api/unknown", {})[0], 400)
        self.assertEqual(self.task()["queue"], "跟进中")
        self.assertEqual(self.task()["notes"], [])

    def test_note_ownership_pin_done_and_missing_note(self):
        for text in ("第一条", "第二条"):
            self.post("/api/notes", {"task_id": "pet", "text": text})
        self.post("/api/note-state", {"task_id": "pet", "note_id": 1, "field": "pinned", "value": True})
        self.post("/api/note-state", {"task_id": "pet", "note_id": 2, "field": "done", "value": True})
        valid = {"task_id": "pet", "note_id": 1, "field": "done", "value": True}
        for invalid in ({"task_id": "environment"}, {"note_id": 99}, {"note_id": True}, {"note_id": []}, {"field": "text"}, {"value": 1}):
            self.assertEqual(self.request("/api/note-state", {**valid, **invalid})[0], 400)
        notes = self.task()["notes"]
        self.assertEqual((notes[0]["id"], notes[0]["pinned"], notes[0]["done"]), (1, 1, 0))
        self.assertEqual(notes[1]["done"], 1)

    def test_add_task_does_not_duplicate_existing_assignment(self):
        self.post("/api/add-task", {"entry_id": "codex:pet"})
        self.assertEqual(len(self.data()["tasks"]), 3)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: self.post("/api/add-task", {"entry_id": "codex:new"}), range(8)))
        self.assertEqual(len(self.data()["tasks"]), 4)
        self.assertEqual(self.task("manual:codex:new")["queue"], "跟进中")
        self.assertEqual(self.request("/api/add-task", {"entry_id": "codex:untracked"})[0], 400)

    def test_allowlist_applies_even_to_cached_snapshot(self):
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        self.write_json("tracking.json", {"mode": "allowlist", "source_ids": ["codex:pet"]})
        data = self.data()
        self.assertEqual([entry["id"] for entry in data["entries"]], ["codex:pet"])
        self.assertEqual([task["id"] for task in data["tasks"]], ["pet"])
        self.assertEqual(data["statistics"]["chat_entries"], 1)
        self.assertEqual(self.request("/api/queue", {"task_id": "environment", "queue": "已完成"})[0], 400)
        self.assertEqual(self.task()["queue"], "已完成")

    def test_source_failure_preserves_first_missing_time_and_user_state(self):
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        original = copy.deepcopy(self.snapshot["entries"])
        self.snapshot["entries"] = [{"id": "codex:pet", "category": "未绑定请求", "route": {"last_status": 200}}]
        self.snapshot["errors"] = ["模拟读取失败"]
        self.post("/api/refresh", {})
        first = self.task()["sources"][0]
        self.assertTrue(first["source_stale"])
        self.assertEqual(first["category"], "聊天")
        self.assertEqual(first["route"]["last_status"], 200)
        self.snapshot.update(entries=[], scanned_at="2026-11-11T20:00:00+08:00")
        self.post("/api/refresh", {})
        self.assertEqual(self.task()["sources"][0]["missing_since_scan"], first["missing_since_scan"])
        self.snapshot.update(entries=original, errors=[])
        self.post("/api/refresh", {})
        self.assertNotIn("source_stale", self.task()["sources"][0])
        self.assertEqual(self.task()["queue"], "已完成")

    def test_database_context_closes_and_rolls_back(self):
        with app.database() as connection:
            connection.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with self.assertRaises(ValueError):
            with app.database() as connection:
                connection.execute("INSERT INTO tasks VALUES ('pet', '已完成')")
                raise ValueError("rollback")
        self.assertEqual(self.task()["queue"], "跟进中")

    def test_export_reflects_saved_completion(self):
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        app.export_report(self.data())
        report = (self.root / "盘点报告.md").read_text()
        self.assertIn("队列：已完成", report)
        self.assertNotIn(app.TOKEN, report)

    def test_planned_branches_survive_refresh_and_export_without_becoming_tasks(self):
        analysis = json.loads((self.root / "analysis.json").read_text())
        branches = [{"name": name, "focus": "预留研究方向。"} for name in ("科研", "工业", "社会")]
        analysis["tasks"][0]["planned_branches"] = branches
        self.write_json("analysis.json", analysis)
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        self.post("/api/notes", {"task_id": "pet", "text": "保留原提醒"})
        self.post("/api/refresh", {})
        data = self.data()
        self.assertEqual(len(data["tasks"]), 3)
        self.assertEqual(self.task("environment")["planned_branches"], branches)
        self.assertEqual(self.task()["queue"], "已完成")
        self.assertEqual(self.task()["notes"][0]["text"], "保留原提醒")
        with app.database() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM custom_tasks").fetchone()[0], 0)
        app.export_report(data)
        report = (self.root / "盘点报告.md").read_text()
        for branch in branches:
            self.assertIn("预留分支：**" + branch["name"] + "**", report)
        self.assertEqual(report.count("等待用户创建聊天，尚未关联，不计入任务数"), 3)

    def milestone(self, **overrides):
        return {"id": "test-milestone-001", "task_id": "pet", "kind": "实现", "focus": "外观", "title": "完成第一部分", "detail": "先完成外观定义，尚未安装。", "occurred_on": "2026-11-10", **overrides}

    def test_timeline_persists_and_does_not_change_queue_notes_or_summary(self):
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        self.post("/api/notes", {"task_id": "pet", "text": "保留提醒"})
        before = self.task()
        self.post("/api/timeline", self.milestone())
        app.initialize()
        self.post("/api/refresh", {})
        task = self.task()
        for field in ("queue", "notes", "stage", "progress", "needs_review"):
            self.assertEqual(task[field], before[field])
        self.assertEqual(len(task["timeline"]), 2)
        self.assertEqual(task["timeline"][0]["origin"], "queue")
        self.assertEqual(task["timeline"][0]["title"], "用户确认结项")
        event = task["timeline"][1]
        self.assertEqual((event["origin"], event["precision"], event["occurred_at"]), ("manual", "day", "2026-11-10"))
        self.assertEqual(self.task("environment")["timeline"], [])
        app.export_report(self.data())
        self.assertIn("完成第一部分", (self.root / "盘点报告.md").read_text())

    def test_timeline_duplicate_requests_are_idempotent_and_owned(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: self.post("/api/timeline", self.milestone()), range(8)))
        self.assertEqual(len(self.task()["timeline"]), 1)
        for overrides in ({"title": "另一个标题"}, {"task_id": "environment"}, {"task_id": "missing"}):
            self.assertEqual(self.request("/api/timeline", self.milestone(**overrides))[0], 400)
        self.assertEqual(self.task("environment")["timeline"], [])

    def test_short_note_display_and_retry_preserve_original_payload(self):
        body = self.milestone(detail="变化：改走新接口。\n结果：程序通过。\n待验：面板。")
        self.post("/api/timeline", body)
        self.post("/api/timeline", body)
        timeline = self.task()["timeline"]
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["detail"], body["detail"])
        self.assertEqual(timeline[0]["points"][-1], {"label": "待验", "text": "面板。"})
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        queue_event = self.task()["timeline"][0]
        self.assertEqual(queue_event["points"][0]["text"], "改为「已完成」。")
        self.assertIn("不代表成果验收", queue_event["points"][1]["text"])
        self.assertEqual(self.task()["queue"], "已完成")

    def test_timeline_invalid_payloads_and_authentication(self):
        invalids = [{"id": []}, {"id": "a"}, {"kind": "完成"}, {"kind": {}}, {"title": " "}, {"title": "长" * 81}, {"focus": "长" * 41}, {"detail": None}, {"detail": "长" * 1201}, {"occurred_on": "2026-02-30"}, {"occurred_on": "2026-1-1"}, {"occurred_on": "9999-12-31"}, {"occurred_on": 123}]
        for overrides in invalids:
            self.assertEqual(self.request("/api/timeline", self.milestone(**overrides))[0], 400, overrides)
        self.assertEqual(self.request("/api/timeline", self.milestone(), headers={"X-Manager-Token": "wrong"})[0], 403)
        self.assertEqual(self.task()["timeline"], [])
        self.post("/api/timeline", self.milestone(detail="sk-secret123456789012345678901234567890"))
        self.assertEqual(self.task()["timeline"][0]["detail"], "[已隐藏密钥]")

    def test_timeline_merges_reviewed_history_queue_and_manual_without_scan_noise(self):
        event = {"id": "review:pet", "task_id": "pet", "occurred_at": "2026-11-09T16:00:00+08:00", "recorded_at": "2026-11-12", "precision": "second", "kind": "想法", "focus": "外观", "title": "起点", "detail": "明确方向", "evidence": [{"source_id": "codex:pet", "ordinal": 9, "text": "用户要求"}, {"source_id": "codex:untracked", "ordinal": 1, "text": "不应暴露"}]}
        self.write_json("timeline.json", {"events": [event]})
        self.post("/api/timeline", self.milestone())
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        self.post("/api/queue", {"task_id": "pet", "queue": "已完成"})
        before = self.task()["timeline"]
        self.assertEqual([item["origin"] for item in before], ["queue", "manual", "review"])
        self.assertEqual(len(before[-1]["evidence"]), 1)
        self.snapshot["entries"][1]["fingerprint"] = "new-messages"
        self.post("/api/refresh", {})
        self.assertTrue(self.task()["needs_review"])
        self.assertEqual(self.task()["timeline"], before)
        self.snapshot["entries"] = []
        self.post("/api/refresh", {})
        self.assertEqual(self.task()["timeline"], before)
        self.write_json("tracking.json", {"mode": "allowlist", "source_ids": ["codex:environment"]})
        self.assertEqual([task["id"] for task in self.data()["tasks"]], ["environment"])
        self.assertEqual(self.task("environment")["timeline"], [])

    def test_timeline_old_date_revisions_append_and_order_stably(self):
        self.post("/api/timeline", self.milestone())
        self.post("/api/timeline", self.milestone(id="test-milestone-002", title="更早阶段", occurred_on="2026-11-08"))
        self.post("/api/timeline", self.milestone(id="test-milestone-003", title="补充说明", occurred_on="2026-11-10"))
        events = self.task()["timeline"]
        self.assertEqual([event["title"] for event in events], ["补充说明", "完成第一部分", "更早阶段"])
        self.assertEqual(events, self.task()["timeline"])

    def test_catalog_and_activity_endpoints_are_read_only(self):
        with patch.object(app, "search_projects", return_value={"projects": []}) as search, patch.object(app, "read_activity", return_value={"statuses": {}}) as activity:
            self.assertEqual(self.request("/api/claude-projects?q=hello%20world")[0], 200)
            search.assert_called_once_with("hello world")
            self.assertEqual(self.request("/api/activity")[0], 200)
            activity.assert_called_once_with({entry["id"] for entry in self.snapshot["entries"]}, gateway_snapshot=app.FEED.current())
            self.assertEqual(self.request("/api/claude-projects?q=" + "a" * 301)[0], 400)
            self.assertEqual(self.request("/api/claude-projects", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(len(self.data()["tasks"]), 3)
        self.assertEqual(self.task()["queue"], "跟进中")

    def test_activity_stream_shares_snapshot_and_unsubscribes(self):
        snapshot = {"connected": False, "routes": [], "error": "isolated"}
        feed = MagicMock()
        feed.closed = False
        channel = queue.Queue()
        channel.put(snapshot)
        feed.subscribe.return_value = channel
        result = {"checked_at": sources.now(), "statuses": {}, "errors": [], "gateway": {"connected": False}}

        def activity(identities, gateway_snapshot=None):
            self.assertEqual(identities, {entry["id"] for entry in self.snapshot["entries"]})
            self.assertIs(gateway_snapshot, snapshot)
            feed.closed = True
            return result.copy()

        with patch.object(app, "FEED", feed), patch.object(app, "read_activity", side_effect=activity) as read, patch.object(app, "search_projects") as search:
            status, body, headers = self.request("/api/activity/events")
            self.assertEqual(status, 200)
            self.assertIn("text/event-stream", headers["Content-Type"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("retry: 3000", body)
            self.assertIn("event: activity", body)
            event = json.loads(next(line[6:] for line in body.splitlines() if line.startswith("data: ")))
            self.assertEqual(event["transport"], "catboss-subscription")
            self.assertEqual(event["statuses"], {})
            read.assert_called_once()
            search.assert_not_called()
            feed.subscribe.assert_called_once()
            feed.unsubscribe.assert_called_once_with(channel)

    def test_activity_stream_rejects_foreign_origin_before_subscribing(self):
        with patch.object(app, "FEED") as feed:
            for headers in ({"Host": "attacker.example"}, {"Origin": "http://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(self.request("/api/activity/events", headers=headers)[0], 403)
            feed.subscribe.assert_not_called()


class SourceTests(unittest.TestCase):
    def test_unavailable_sources_have_complete_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = sources.collect(home=directory, allowed_ids={"codex:pet"})
            self.assertEqual(snapshot["entries"], [])
            self.assertTrue(snapshot["errors"])
            for field in ("codex_threads", "claude_files", "gateway_files", "import_duplicates", "chat_entries", "inventory_entries", "route_requests"):
                self.assertEqual(snapshot["statistics"][field], 0)

    def test_corrupt_import_metadata_does_not_abort_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / ".codex/external_agent_session_imports.json"
            metadata.parent.mkdir()
            for content in ("{", "[]", '{"records": null}', '{"records": [{}]}'):
                metadata.write_text(content)
                snapshot = sources.collect(home=directory, allowed_ids={"codex:pet"})
                self.assertTrue(any("导入映射读取失败" in error for error in snapshot["errors"]))

    def test_credential_masking(self):
        self.assertNotIn("sk-secret", sources.safe_text("sk-secret123456789012345678901234567890"))


if __name__ == "__main__":
    unittest.main()
