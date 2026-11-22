import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activity import read_activity
from codex_records import read_segment, replacement_segment
from sources import collect


class CurrentRolloutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        root = self.home / ".codex"
        root.mkdir()
        self.path = root / "current.jsonl"
        self.header = {"type": "session_meta", "ordinal": 288, "payload": {"id": "industry", "timestamp": "2026-11-12T03:40:41Z"}}
        self.records = [self.header, self.event("task_started", turn_id="new", started_at=1794454842), self.message("user", "新请求", 300), self.message("assistant", "新报告已交付", 568), self.event("task_complete", turn_id="new", completed_at=1794456752)]
        self.write()
        with sqlite3.connect(root / "state_5.sqlite") as connection:
            connection.execute("CREATE TABLE threads (id TEXT, name TEXT, title TEXT, cwd TEXT, source TEXT, model_provider TEXT, updated_at INTEGER, archived INTEGER, rollout_path TEXT)")
            connection.execute("INSERT INTO threads VALUES ('industry','工业','工业','/work','vscode','catboss',1794456752,0,?)", (str(self.path),))
        with sqlite3.connect(root / "thread_history_1.sqlite") as connection:
            connection.execute("CREATE TABLE thread_turns (thread_id TEXT, status TEXT, started_at INTEGER, completed_at INTEGER, rollout_ordinal INTEGER)")
            connection.execute("INSERT INTO thread_turns VALUES ('industry','failed',1794454676,1794454756,419)")
            connection.execute("CREATE TABLE thread_items (thread_id TEXT, item_type TEXT, item_json TEXT, rollout_ordinal INTEGER)")
            for ordinal, text in ((10, "共同背景"), (300, "旧失败分支内容")):
                item = {"type": "userMessage", "content": [{"type": "text", "text": text}]}
                connection.execute("INSERT INTO thread_items VALUES ('industry','userMessage',?,?)", (json.dumps(item), ordinal))
        self.gateway = {"connected": False, "routes": [], "error": "isolated"}
        self.addCleanup(read_segment.cache_clear)

    def event(self, kind, **payload):
        return {"type": "event_msg", "payload": {"type": kind, **payload}}

    def message(self, role, text, ordinal):
        return {"ordinal": ordinal, **self.event("item_completed", item={"id": str(ordinal), "type": "UserMessage" if role == "user" else "AgentMessage", "content": [{"type": "Text", "text": text}]})}

    def write(self):
        self.path.write_text("\n".join(json.dumps(record) for record in self.records) + "\n")

    def test_replaced_file_beats_failed_index_for_both_history_and_activity(self):
        snapshot = collect(home=self.home, allowed_ids={"codex:industry"})
        self.assertFalse(snapshot["errors"])
        entry = snapshot["entries"][0]
        self.assertEqual(entry["message_count"], 3)
        self.assertEqual(entry["latest_request"], "新请求")
        self.assertEqual(entry["latest_response"], "新报告已交付")
        self.assertEqual(entry["saved_turn_status"]["status"], "completed")
        self.assertNotIn("旧失败分支内容", json.dumps(entry, ensure_ascii=False))
        activity = read_activity({"codex:industry"}, self.home, self.gateway)
        self.assertFalse(activity["errors"])
        self.assertEqual(activity["statuses"]["codex:industry"]["label"], "本轮已结束")

    def test_compaction_tool_text_and_duplicate_response_are_not_dialogue(self):
        self.records.extend([
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "private-compaction"}},
            self.event("item_completed", item={"id": "tool", "type": "McpToolCall", "result": "private-tool-output"}),
            self.records[3],
        ])
        self.write()
        entry = collect(home=self.home, allowed_ids={"codex:industry"})["entries"][0]
        self.assertEqual(entry["message_count"], 3)
        self.assertNotIn("private-", json.dumps(entry))

    def test_new_turn_invalidates_cache_and_unmatched_completion_does_not_finish_it(self):
        first = collect(home=self.home, allowed_ids={"codex:industry"})["entries"][0]
        self.records.extend([self.event("task_started", turn_id="next", started_at=1794457000), self.message("user", "接着研究", 580), self.event("task_complete", turn_id="new", completed_at=1794456752)])
        self.write()
        updated = collect(home=self.home, allowed_ids={"codex:industry"})["entries"][0]
        self.assertNotEqual(first["fingerprint"], updated["fingerprint"])
        self.assertEqual(updated["latest_request"], "接着研究")
        self.assertEqual(updated["saved_turn_status"]["status"], "inProgress")
        with self.path.open("a") as stream:
            stream.write('{"unfinished":')
        partial = collect(home=self.home, allowed_ids={"codex:industry"})
        self.assertFalse(partial["errors"])
        self.assertEqual(partial["entries"][0]["fingerprint"], updated["fingerprint"])

    def test_identity_mismatch_and_invalid_json_fail_closed(self):
        for records in ([{**self.header, "payload": {"id": "another"}}], [self.header, "not-a-record"]):
            self.records = records
            self.write()
            if records[-1] == "not-a-record":
                self.path.write_text(json.dumps(self.header) + "\ninvalid-json\n")
            snapshot = collect(home=self.home, allowed_ids={"codex:industry"})
            self.assertTrue(snapshot["errors"])
            self.assertTrue(snapshot["entries"][0]["source_stale"])
            activity = read_activity({"codex:industry"}, self.home, self.gateway)
            self.assertEqual(activity["statuses"]["codex:industry"]["kind"], "unknown")

    def test_missing_file_or_caught_up_index_keeps_existing_index_path(self):
        self.assertIsNone(replacement_segment(self.path, "industry", {"started_at": 1794454842}))
        self.path.unlink()
        snapshot = collect(home=self.home, allowed_ids={"codex:industry"})
        self.assertFalse(snapshot["errors"])
        self.assertEqual(snapshot["entries"][0]["latest_request"], "旧失败分支内容")
        self.assertEqual(snapshot["entries"][0]["saved_turn_status"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
