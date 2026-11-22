import json
import shlex
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activity import read_activity, status_record
from catalog import CACHE, search_projects


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        CACHE.clear()
        self.addCleanup(CACHE.clear)

    def session(self, project, identity, text, cwd="/work/my project"):
        path = self.home / ".claude/projects" / project / (identity + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {"type": "user", "cwd": cwd, "message": {"content": text}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "项目正文 Needle"}, {"type": "tool_use", "input": "private-tool-output"}]}},
            {"type": "user", "isSidechain": True, "message": {"content": "sidechain-secret"}},
            {"type": "api-request", "params": {"token": "never-return-this"}},
        ]
        path.write_text("\n".join(json.dumps(record) for record in records))
        return path

    def test_group_search_body_path_title_and_no_hidden_payload(self):
        self.session("one", "session-one", "你好 Test")
        self.session("one", "session-two", "另一个任务")
        self.session("two", "session-three", "其他项目", cwd="/work/second")
        all_results = search_projects(home=self.home)
        self.assertEqual(all_results["project_count"], 2)
        self.assertEqual(all_results["session_count"], 3)
        for query, count in (("test", 1), ("my project", 2), ("needle", 3), ("不存在", 0), ("session-two", 1), ("sidechain-secret", 0)):
            self.assertEqual(search_projects(query, self.home)["matched_count"], count, query)
        encoded = json.dumps(all_results)
        for secret in ("search_text", "private-tool-output", "sidechain-secret", "never-return-this"):
            self.assertNotIn(secret, encoded)

    def test_changed_and_removed_sessions_invalidate_cache(self):
        path = self.session("one", "session-one", "before")
        self.assertEqual(search_projects("before", self.home)["matched_count"], 1)
        self.session("one", "session-one", "after-changed")
        self.assertEqual(search_projects("before", self.home)["matched_count"], 0)
        self.assertEqual(search_projects("after-changed", self.home)["matched_count"], 1)
        path.unlink()
        self.assertEqual(search_projects(home=self.home)["session_count"], 0)

    def test_resume_command_quotes_untrusted_paths_and_redacts_secrets(self):
        cwd = "/work/test'; echo unsafe; $(touch no)"
        self.session("one", "session-one", "sk-secret123456789012345678901234567890", cwd=cwd)
        result = search_projects(home=self.home)
        session = result["projects"][0]["sessions"][0]
        self.assertEqual(session["resume_command"], "cd -- " + shlex.quote(cwd) + " && claude --resume session-one")
        self.assertNotIn("sk-secret", json.dumps(result))

    def test_missing_empty_sidechain_and_bad_json(self):
        self.assertFalse(search_projects(home=self.home)["available"])
        path = self.session("one", "session-one", "keep")
        with path.open("a") as stream:
            stream.write("\nnot-json\n[]\n")
        nested = path.parent / "subagents/agent-hidden.jsonl"
        nested.parent.mkdir()
        nested.write_text('{"type":"user","message":{"content":"nested-secret"}}')
        (path.parent / "title-only.jsonl").write_text('{"type":"ai-title","aiTitle":"只有标题"}')
        result = search_projects(home=self.home)
        self.assertEqual(result["session_count"], 1)
        self.assertEqual(search_projects("nested-secret", self.home)["matched_count"], 0)

    def test_chat_timestamp_takes_precedence_over_file_modification(self):
        path = self.session("one", "session-one", "keep")
        with path.open("a") as stream:
            stream.write('\n{"type":"user","timestamp":"2026-01-01T01:00:00Z","message":{"content":"dated"}}')
        session = search_projects(home=self.home)["projects"][0]["sessions"][0]
        self.assertEqual(session["updated"], "2026-01-01T09:00:00+08:00")
        self.assertEqual(session["time_basis"], "最后对话")


class ActivityTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("activity.read_gateway", return_value={"connected": False, "routes": [], "error": "test offline"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_active_record_expires_and_completion_is_not_task_completion(self):
        clock = datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp()
        for raw, kind in (("inProgress", "running"), ("working", "running"), ("completed", "idle"), ("failed", "error"), ("interrupted", "paused"), ({}, "unknown")):
            self.assertEqual(status_record(raw, clock, "test", clock)["kind"], kind)
        self.assertEqual(status_record("inProgress", clock - 121, "test", clock)["kind"], "unknown")
        self.assertEqual(status_record("inProgress", clock + 60, "test", clock)["kind"], "unknown")
        self.assertEqual(status_record("idle", clock - 121, "test", clock)["kind"], "unknown")
        self.assertEqual(status_record("completed", clock - 3600, "test", clock)["label"], "本轮已结束")

    def test_reads_fresh_local_status_without_importing_chats(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            with sqlite3.connect(home / ".codex/state_5.sqlite") as connection:
                connection.execute("CREATE TABLE threads (id TEXT, updated_at INTEGER, rollout_path TEXT)")
                connection.execute("INSERT INTO threads VALUES ('tracked',?,NULL)", (datetime.now().timestamp(),))
                connection.execute("INSERT INTO threads VALUES ('untracked',?,NULL)", (datetime.now().timestamp(),))
            with sqlite3.connect(home / ".codex/thread_history_1.sqlite") as connection:
                connection.execute("CREATE TABLE thread_turns (thread_id TEXT, status TEXT, started_at INTEGER, completed_at INTEGER, rollout_ordinal INTEGER)")
                connection.execute("INSERT INTO thread_turns VALUES ('tracked','completed',0,0,1)")
                connection.execute("INSERT INTO thread_turns VALUES ('tracked','inProgress',0,NULL,2)")
            sessions = home / ".claude/sessions"
            sessions.mkdir(parents=True)
            (sessions / "session.json").write_text(json.dumps({"sessionId": "claude-one", "status": "idle", "updatedAt": datetime.now().timestamp() * 1000, "secret": "not-visible"}))
            result = read_activity({"codex:tracked"}, home)
            self.assertEqual(result["statuses"]["codex:tracked"]["kind"], "running")
            self.assertNotIn("codex:untracked", result["statuses"])
            self.assertEqual(result["statuses"]["claude:claude-one"]["label"], "空闲")
            self.assertNotIn("not-visible", json.dumps(result))
            self.assertEqual(result["errors"], [])

    def test_source_unavailable_returns_unknown_not_old_running(self):
        with tempfile.TemporaryDirectory() as directory:
            result = read_activity({"codex:tracked"}, directory)
            self.assertEqual(result["statuses"], {})
            self.assertEqual(len(result["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
