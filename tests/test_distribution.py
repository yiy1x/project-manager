import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import demo
from configuration import initialize_data, source_home, trace_url
from sources import collect
from timeline import KINDS, for_task, short_points


class DistributionTests(unittest.TestCase):
    def test_empty_install_is_private_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_data(root)
            self.assertEqual(json.loads((root / "tracking.json").read_text())["source_ids"], [])
            self.assertFalse(json.loads((root / "analysis_settings.json").read_text())["enabled"])
            (root / "analysis.json").write_text('{"tasks": ["keep"]}')
            initialize_data(root)
            self.assertEqual(json.loads((root / "analysis.json").read_text())["tasks"], ["keep"])
            with self.assertRaises(ValueError):
                initialize_data(root, demo=True)

    def test_demo_data_is_separate_and_cannot_become_live(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_data(root, demo=True)
            self.assertEqual(len(json.loads((root / "analysis.json").read_text())["tasks"]), 2)
            with self.assertRaises(ValueError):
                initialize_data(root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep.txt").write_text("private")
            with self.assertRaises(ValueError):
                initialize_data(root, demo=True)

    def test_empty_allowlist_never_reads_source_home(self):
        with patch("sources.source_home", side_effect=AssertionError("must not scan")):
            self.assertEqual(collect(allowed_ids=set())["entries"], [])

    def test_source_home_is_configurable_and_explicit_argument_wins(self):
        with patch.dict(os.environ, {"MANAGER_SOURCE_HOME": "/tmp/example-home"}):
            self.assertEqual(source_home(), Path("/tmp/example-home").resolve())
            self.assertEqual(source_home("/tmp/other-home"), Path("/tmp/other-home").resolve())

    def test_trace_url_accepts_only_local_unauthenticated_endpoints(self):
        for value in ("http://127.0.0.1:3425/v1/catboss/trace", "http://localhost:9999/trace", "http://[::1]:3425/trace"):
            with patch.dict(os.environ, {"CATBOSS_TRACE_URL": value}):
                self.assertEqual(trace_url(), value)
        for value in ("https://example.com/trace", "http://127.0.0.1.evil.test/", "http://user:secret@localhost/", "file:///tmp/trace", "http://localhost/?token=secret", "http://localhost/#token", "http://localhost:99999/"):
            with patch.dict(os.environ, {"CATBOSS_TRACE_URL": value}), self.assertRaises(ValueError):
                trace_url()

    def test_demo_snapshot_and_activity_never_read_local_sources(self):
        with tempfile.TemporaryDirectory() as directory, patch.multiple(app, ROOT=Path(directory), DATABASE=Path(directory) / "test.sqlite3", DEMO=True, LAST_SCAN=0.0), patch.object(app, "collect", side_effect=AssertionError("local scan")), patch.object(app, "read_activity", side_effect=AssertionError("live activity")):
            app.initialize()
            app.refresh(True)
            state = app.state()
            self.assertTrue(state["demo"])
            self.assertEqual(len(state["tasks"]), 2)
            self.assertFalse(state["auto_review"]["enabled"])
            self.assertEqual(app.current_activity()["live_requests"], [])
            with self.assertRaises(ValueError):
                app.set_auto_review(True)

    def test_tracking_is_explicit_and_idempotent(self):
        snapshot = {"entries": [{"id": "codex:example", "title": "示例", "category": "聊天"}], "errors": []}
        with tempfile.TemporaryDirectory() as directory, patch.multiple(app, ROOT=Path(directory), DATABASE=Path(directory) / "test.sqlite3", DEMO=False), patch.object(app, "collect", return_value=snapshot):
            app.initialize()
            app.track_sources(["codex:example"])
            app.track_sources(["codex:example"])
            self.assertEqual(app.allowed_sources(), {"codex:example"})
            with app.database() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM custom_tasks").fetchone()[0], 1)
            with self.assertRaises(ValueError):
                app.track_sources(["codex:missing"])
            self.assertEqual(app.allowed_sources(), {"codex:example"})

    def test_missing_allowlist_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "ROOT", Path(directory)):
            with self.assertRaises(ValueError):
                app.allowed_sources()


class TimelineTests(unittest.TestCase):
    def test_demo_history_has_unique_bounded_nodes(self):
        events = demo.history()["events"]
        self.assertEqual(len({event["id"] for event in events}), len(events))
        for event in events:
            self.assertIn(event["kind"], KINDS)
            self.assertLessEqual(len(event["summary"]), 40)
            self.assertEqual(event["evidence"], [])
            self.assertTrue(all(len(point["label"]) <= 4 and len(point["text"]) <= 48 for point in event["points"]))

    def test_explicit_short_lines_are_not_semantic_summaries(self):
        self.assertEqual(short_points("变化：新增筛选。\n待验：状态计数。"), [{"label": "变化", "text": "新增筛选。"}, {"label": "待验", "text": "状态计数。"}])
        for text in ("没有标签的长段落不截断。", "结果：", "这是超长标签：内容", "结果：" + "字" * 49, "结果：内容\n" * 4):
            self.assertEqual(short_points(text), [])

    def test_rendering_keeps_original_history(self):
        event = {"id": "one", "task_id": "task", "detail": "结果：程序通过。\n待验：面板。", "occurred_at": "2026-11-10", "recorded_at": "2026-11-10", "evidence": []}
        rendered = for_task([event], {"id": "task", "source_ids": []})[0]
        self.assertEqual(rendered["detail"], event["detail"])
        self.assertEqual(len(rendered["points"]), 2)
        self.assertNotIn("points", event)
