import copy
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_review
from review_source import fingerprint, read_source, read_tail


class CompletionReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        (self.home / ".codex").mkdir()
        self.path = self.home / "rollout.jsonl"
        self.records = [
            {"type": "session_meta", "ordinal": 0, "timestamp": "2026-11-13T10:00:00+08:00", "payload": {"id": "tracked", "timestamp": "2026-11-13T10:00:00+08:00"}},
            self.event(1, "task_started", turn_id="turn-one", started_at=1794535800),
            self.message(2, "UserMessage", "要做的事"),
            self.message(3, "AgentMessage", "已经完成，发布待确认", phase="final_answer"),
            self.event(4, "task_complete", turn_id="turn-one", completed_at=1794535900),
        ]
        with sqlite3.connect(self.home / ".codex/state_5.sqlite") as connection:
            connection.execute("CREATE TABLE threads(id TEXT,rollout_path TEXT)")
            connection.execute("INSERT INTO threads VALUES ('tracked',?)", (str(self.path),))
        with sqlite3.connect(self.home / ".codex/thread_history_1.sqlite") as connection:
            connection.execute("CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,rollout_ordinal INTEGER,rollout_byte_offset INTEGER,status TEXT,started_at INTEGER,completed_at INTEGER)")
            connection.execute("INSERT INTO thread_turns VALUES ('tracked','turn-one',1,0,'completed',1794535800,1794535900)")
            connection.execute("CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_id TEXT,item_type TEXT,rollout_ordinal INTEGER,created_at_ms INTEGER,item_json TEXT)")
        self.write()
        self.addCleanup(read_tail.cache_clear)

    def event(self, ordinal, kind, **payload):
        return {"ordinal": ordinal, "timestamp": "2026-11-13T10:05:00+08:00", "type": "event_msg", "payload": {"type": kind, **payload}}

    def message(self, ordinal, kind, text, **extra):
        return self.event(ordinal, "item_completed", item={"id": "message-" + str(ordinal), "type": kind, "content": [{"type": "Text", "text": text}], **extra})

    def write(self):
        self.path.write_text("\n".join(json.dumps(record) for record in self.records) + "\n")

    def test_completion_requires_matching_turn_and_final_reply(self):
        source = read_source("codex:tracked", self.home)
        self.assertTrue(source["completed"])
        self.assertEqual(len(source["messages"]), 2)
        self.records[-1]["payload"]["turn_id"] = "another"
        self.write()
        self.assertFalse(read_source("codex:tracked", self.home)["completed"])
        self.records[-1]["payload"]["turn_id"] = "turn-one"
        self.records[3]["payload"]["item"]["phase"] = "commentary"
        self.write()
        self.assertFalse(read_source("codex:tracked", self.home)["completed"])

    def test_new_turn_in_file_overrides_stale_completed_index(self):
        self.records.extend([self.event(5, "task_started", turn_id="turn-two", started_at=1794536000), self.message(6, "UserMessage", "继续修改")])
        self.write()
        source = read_source("codex:tracked", self.home)
        self.assertFalse(source["completed"])
        self.assertEqual(source["turn_key"], "turn-two")
        self.records.append(self.event(7, "task_complete", turn_id="turn-one", completed_at=1794536100))
        self.write()
        self.assertFalse(read_source("codex:tracked", self.home)["completed"])

    def test_unstarted_followup_and_partial_line_do_not_reuse_old_completion(self):
        self.records.append(self.message(5, "UserMessage", "另一项要求"))
        self.write()
        with self.path.open("a") as stream:
            stream.write('{"unfinished":')
        self.assertFalse(read_source("codex:tracked", self.home)["completed"])

    def test_completed_prefix_survives_immediate_next_turn_and_new_index(self):
        offset = self.path.stat().st_size
        self.records.extend([self.event(5, "task_started", turn_id="turn-two", started_at=1794536000), self.message(6, "UserMessage", "下一轮尚未完成")])
        self.write()
        with sqlite3.connect(self.home / ".codex/thread_history_1.sqlite") as connection:
            connection.execute("INSERT INTO thread_turns VALUES ('tracked','turn-two',5,?,'inProgress',1794536000,NULL)", (offset,))
        source = read_source("codex:tracked", self.home)
        self.assertFalse(source["completed"])
        self.assertEqual(source["turn_key"], "turn-two")
        completed = source["completed_prefix"]
        self.assertTrue(completed["completed"])
        self.assertEqual(completed["turn_key"], "turn-one")
        self.assertEqual([message["ordinal"] for message in completed["messages"]], [2, 3])
        self.assertEqual(completed["fingerprint"], fingerprint(source["messages"][:2]))

    def test_tools_compaction_and_repeated_message_are_excluded(self):
        self.records.extend([self.records[3], {"type": "response_item", "payload": {"role": "assistant", "content": "压缩摘要"}}, self.event(8, "item_completed", item={"id": "tool", "type": "McpToolCall", "result": "秘密工具结果"})])
        self.write()
        source = read_source("codex:tracked", self.home)
        self.assertEqual(len(source["messages"]), 2)
        self.assertNotIn("秘密", json.dumps(source, ensure_ascii=False))


class ExecutionQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "manager.sqlite3"
        self.task = {"id": "task", "name": "测试任务", "source_ids": ["codex:tracked"], "goal": "目标", "stage": "旧阶段", "progress": "旧进度", "blocker": "待确认", "next": "下一步"}
        self.write("analysis.json", {"tasks": [self.task, {**self.task, "id": "manager", "source_ids": ["codex:manager"]}, {**self.task, "id": "paused", "source_ids": ["codex:paused"]}]})
        self.write("tracking.json", {"mode": "allowlist", "source_ids": ["codex:tracked", "codex:manager", "codex:paused"]})
        self.write("timeline.json", {"events": [{"task_id": "task", "summary": "之前已经回读", "kind": "实现", "focus": "实现", "points": [], "evidence": [{"source_id": "codex:tracked", "ordinal": 2}]}]})
        self.write("analysis_settings.json", {**auto_review.DEFAULTS, "enabled": True})
        self.messages = [self.message(1, "user", "初始要求"), self.message(2, "assistant", "旧回复"), self.message(3, "user", "新增功能"), self.message(4, "assistant", "功能已完成，尚未发布")]
        self.source = {"source_id": "codex:tracked", "messages": self.messages, "fingerprint": fingerprint(self.messages), "turn_key": "turn-two", "status": "completed", "completed": True}
        self.initial_fingerprint = fingerprint(self.messages[:2])
        self.result = {"goal": "继续完成原目标", "stage": "功能完成，发布待确认", "progress": "原助手报告功能已完成。", "blocker": "等待发布确认", "next": "确认预览", "events": [{"summary": "完成新增功能，等待确认预览", "kind": "实现", "focus": "功能实现", "detail": "原助手交付新增功能，还没有发布。", "points": [{"label": "成果", "text": "已完成新增功能。"}, {"label": "待确认", "text": "预览确认后再发布。"}], "evidence_ids": ["message-4"]}]}
        self.result["next_plan"] = {"summary": "先确认预览，再决定是否发布", "points": [{"label": "确认", "text": "检查预览效果，确认后再发布。"}]}
        self.result["confirmation_plan"] = {"summary": "发布仍待用户确认", "points": [{"label": "授权", "text": "预览交付不等于获得公开发布授权。"}]}
        with sqlite3.connect(self.database) as connection:
            connection.executescript("CREATE TABLE tasks(id TEXT PRIMARY KEY,queue TEXT); CREATE TABLE baselines(id TEXT PRIMARY KEY,fingerprint TEXT); CREATE TABLE custom_tasks(payload TEXT); CREATE TABLE notes(id INTEGER,text TEXT); CREATE TABLE events(id INTEGER,action TEXT); CREATE TABLE milestones(id TEXT,payload TEXT);")
            connection.execute("INSERT INTO tasks VALUES ('paused','暂不跟进')")
            connection.execute("INSERT INTO notes VALUES (1,'保留提醒')")
            connection.execute("INSERT INTO baselines VALUES ('codex:tracked',?)", (self.initial_fingerprint,))
            auto_review.initialize(connection)
        self.feed = MagicMock()
        self.feed.current.return_value = {"routes": []}
        self.reader = MagicMock(side_effect=lambda identity: copy.deepcopy(self.source))
        self.runner = MagicMock(side_effect=lambda packet, options: copy.deepcopy(self.result))
        self.service = auto_review.ReviewService(self.root, self.database, threading.RLock(), self.feed, reader=self.reader, runner=self.runner)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False))

    def message(self, ordinal, role, text):
        return {"ordinal": ordinal, "message_id": "message-" + str(ordinal), "role": role, "text": text, "phase": "final_answer" if role == "assistant" else None, "occurred_at": "2026-11-13T10:0" + str(ordinal) + ":00+08:00", "record_path": "", "turn_id": "turn-two"}

    def due(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE review_jobs SET available_at=0")

    def jobs(self):
        with self.service.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM review_jobs")]

    def test_only_finished_followed_tasks_enqueue_and_duplicate_scan_is_idempotent(self):
        self.source["completed"] = False
        self.service.scan()
        self.assertEqual(self.jobs(), [])
        self.source["completed"] = True
        self.feed.current.return_value = {"routes": [{"source_id": "codex:tracked", "done": False}]}
        self.service.scan()
        self.assertEqual(self.jobs(), [])
        self.feed.current.return_value = {"routes": []}
        self.service.scan()
        self.service.scan()
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual({call.args[0] for call in self.reader.call_args_list}, {"codex:tracked"})

    def test_analysis_cursor_result_and_timeline_commit_without_changing_user_state(self):
        self.service.scan()
        self.due()
        self.assertTrue(self.service.process_one())
        self.assertEqual(self.jobs()[0]["status"], "completed")
        self.service.scan()
        self.assertFalse(self.service.process_one())
        self.assertEqual(self.runner.call_count, 1)
        with self.service.connect() as connection:
            self.assertEqual(connection.execute("SELECT fingerprint FROM baselines WHERE id='codex:tracked'").fetchone()[0], self.source["fingerprint"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT text FROM notes").fetchone()[0], "保留提醒")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT queue FROM tasks WHERE id='paused'").fetchone()[0], "暂不跟进")
            task = copy.deepcopy(self.task)
            auto_review.overlays(connection, [task])
            self.assertEqual(task["stage"], self.result["stage"])
            self.assertEqual(task["next_plan"], self.result["next_plan"])
            self.assertEqual(task["confirmation_plan"], self.result["confirmation_plan"])
            self.assertEqual(task["next"], self.result["next"])
        self.assertEqual(self.runner.call_args.args[0]["increment"][0]["id"], "message-3")

    def test_invalid_evidence_retries_then_fails_without_advancing_baseline(self):
        self.result["events"][0]["evidence_ids"] = ["not-read"]
        self.service.scan()
        for expected in ("retry", "retry", "failed"):
            self.due()
            self.service.process_one()
            self.assertEqual(self.jobs()[0]["status"], expected)
        with self.service.connect() as connection:
            self.assertEqual(connection.execute("SELECT fingerprint FROM baselines WHERE id='codex:tracked'").fetchone()[0], self.initial_fingerprint)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_results").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 0)

    def test_new_round_while_analysing_keeps_old_result_and_marks_later_content_unreviewed(self):
        def finish(packet, options):
            self.source["messages"].append(self.message(5, "user", "接着改下一项"))
            self.source.update(fingerprint=fingerprint(self.source["messages"]), turn_key="turn-three", completed=False, status="inProgress")
            return copy.deepcopy(self.result)
        self.runner.side_effect = finish
        self.service.scan()
        self.due()
        self.service.process_one()
        self.assertEqual(self.jobs()[0]["status"], "completed")
        with self.service.connect() as connection:
            cursor = json.loads(connection.execute("SELECT payload FROM review_cursors WHERE task_id='task'").fetchone()[0])
            self.assertEqual(cursor["count"], 4)
            self.assertNotEqual(connection.execute("SELECT fingerprint FROM baselines WHERE id='codex:tracked'").fetchone()[0], self.source["fingerprint"])

    def test_changed_history_or_manual_analysis_prevents_stale_write(self):
        for changed in ("history", "manual"):
            with self.subTest(changed=changed):
                self.service.scan()
                self.due()
                def finish(packet, options):
                    if changed == "history":
                        self.source["messages"][0]["text"] = "重开后的不同历史"
                        self.source["fingerprint"] = fingerprint(self.source["messages"])
                    else:
                        self.write("analysis.json", {"tasks": [{**self.task, "progress": "人工的新分析"}]})
                    return copy.deepcopy(self.result)
                self.runner.side_effect = finish
                self.service.process_one()
                with self.service.connect() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_results").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 0)
                    connection.execute("DELETE FROM review_jobs")
                self.source["messages"][0]["text"] = "初始要求"
                self.source["fingerprint"] = fingerprint(self.source["messages"])

    def test_pausing_task_before_execution_prevents_model_call(self):
        self.service.scan()
        self.due()
        with self.service.connect() as connection:
            connection.execute("INSERT INTO tasks VALUES ('task','暂不跟进')")
        self.service.process_one()
        self.runner.assert_not_called()
        self.assertEqual(self.jobs()[0]["status"], "superseded")

    def test_completed_previous_turn_can_run_while_next_turn_is_active(self):
        completed = copy.deepcopy(self.source)
        self.source["messages"].append(self.message(5, "user", "下一轮进行中"))
        self.source.update(fingerprint=fingerprint(self.source["messages"]), turn_key="turn-three", completed=False, status="inProgress", completed_prefix=completed)
        self.feed.current.return_value = {"routes": [{"source_id": "codex:tracked", "done": False}]}
        self.service.scan()
        self.due()
        self.service.process_one()
        self.assertEqual(self.jobs()[0]["status"], "completed")
        self.assertEqual([message["id"] for message in self.runner.call_args.args[0]["increment"]], ["message-3", "message-4"])
        with self.service.connect() as connection:
            cursor = json.loads(connection.execute("SELECT payload FROM review_cursors").fetchone()[0])
            self.assertEqual(cursor["count"], 4)
            self.assertNotEqual(connection.execute("SELECT fingerprint FROM baselines WHERE id='codex:tracked'").fetchone()[0], self.source["fingerprint"])
        self.service.scan()
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(len(self.jobs()), 1)

    def test_new_completed_turn_supersedes_old_retry_and_failed_jobs(self):
        for status in ("retry", "failed"):
            with self.subTest(status=status):
                self.service.scan()
                with self.service.connect() as connection:
                    connection.execute("UPDATE review_jobs SET status=?", (status,))
                self.source["messages"].extend([self.message(5, "user", "继续改版"), self.message(6, "assistant", "新版已经交付")])
                self.source.update(fingerprint=fingerprint(self.source["messages"]), turn_key="turn-three")
                self.service.scan()
                self.assertEqual([job["status"] for job in self.jobs()], ["superseded", "queued"])
                self.due()
                self.service.process_one()
                self.assertEqual([job["status"] for job in self.jobs()], ["superseded", "completed"])
                with self.service.connect() as connection:
                    state = auto_review.review_state(connection, {**self.task, "queue": "跟进中", "needs_review": False}, [], True)
                    self.assertEqual(state["state"], "current")
                    connection.execute("DELETE FROM review_jobs")
                    connection.execute("DELETE FROM review_cursors")
                    connection.execute("DELETE FROM review_results")
                    connection.execute("UPDATE baselines SET fingerprint=?", (self.initial_fingerprint,))
                self.source["messages"] = self.source["messages"][:4]
                self.source.update(fingerprint=fingerprint(self.source["messages"]), turn_key="turn-two")

    def test_manual_timeline_is_supplied_to_avoid_repeating_user_records(self):
        record = {"id": "manual:one", "task_id": "task", "title": "我已经确认预览", "kind": "验证", "focus": "预览", "detail": "结果：预览已经确认。", "occurred_at": "2026-11-13", "recorded_at": "2026-11-13T10:05:00+08:00"}
        with self.service.connect() as connection:
            connection.execute("INSERT INTO milestones VALUES (?,?)", (record["id"], json.dumps(record, ensure_ascii=False)))
        self.service.scan()
        packet = json.loads(self.jobs()[0]["payload"])["packet"]
        self.assertIn({"summary": record["title"], "kind": "验证", "focus": "预览", "points": [{"label": "结果", "text": "预览已经确认。"}]}, packet["existing_timeline"])

    def test_disabling_during_analysis_does_not_write_result(self):
        def finish(packet, options):
            self.write("analysis_settings.json", {**auto_review.DEFAULTS, "enabled": False})
            return copy.deepcopy(self.result)
        self.runner.side_effect = finish
        self.service.scan()
        self.due()
        self.service.process_one()
        with self.service.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_results").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT fingerprint FROM baselines WHERE id='codex:tracked'").fetchone()[0], self.initial_fingerprint)

    def test_result_timeline_cursor_and_job_roll_back_together(self):
        self.service.scan()
        with self.service.connect() as connection:
            connection.execute("CREATE TRIGGER reject_cursor BEFORE INSERT ON review_cursors BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.due()
        self.service.process_one()
        self.assertEqual(self.jobs()[0]["status"], "retry")
        with self.service.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_results").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 0)
            self.assertEqual(json.loads(connection.execute("SELECT payload FROM review_cursors").fetchone()[0])["count"], 2)
            self.assertEqual(connection.execute("SELECT fingerprint FROM baselines WHERE id='codex:tracked'").fetchone()[0], self.initial_fingerprint)

    def test_export_failure_does_not_repeat_successful_model_analysis(self):
        self.service.on_update = MagicMock(side_effect=OSError("export failed"))
        self.service.scan()
        self.due()
        self.service.process_one()
        self.assertEqual(self.jobs()[0]["status"], "completed")
        self.assertIn("分析已保存", self.service.error)
        self.service.scan()
        self.assertFalse(self.service.process_one())
        self.assertEqual(self.runner.call_count, 1)
        self.assertIn("分析已保存", self.service.error)

    def test_scan_error_clears_after_recovery_without_hiding_another_source_error(self):
        self.reader.side_effect = sqlite3.OperationalError("unable to open database file")
        self.service.scan()
        self.assertIn("测试任务", self.service.error)
        self.assertIn("unable to open database file", self.service.error)
        self.assertTrue(self.service.last_checked_at)
        self.reader.side_effect = lambda identity: copy.deepcopy(self.source)
        self.service.scan()
        self.assertEqual(self.service.error, "")
        self.write("analysis.json", {"tasks": [self.task, {**self.task, "id": "other", "name": "另一任务", "source_ids": ["codex:other"]}]})
        self.write("tracking.json", {"mode": "allowlist", "source_ids": ["codex:tracked", "codex:other"]})
        self.reader.side_effect = [sqlite3.OperationalError("first source failed"), copy.deepcopy(self.source)]
        self.service.scan()
        self.assertIn("first source failed", self.service.error)
        self.assertNotIn("另一任务", self.service.error)

    def test_database_connection_closes_after_scan_context(self):
        with self.service.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_execution_recovery_only_clears_execution_error(self):
        self.service.execution_error = "旧执行错误"
        self.service.scan_error = "来源仍不可读"

        def process():
            self.service.stop.set()
            return False

        self.service.process_one = process
        self.service.wake.set()
        self.service.execute()
        self.assertEqual(self.service.error, "来源仍不可读")


class RunnerSafetyTests(unittest.TestCase):
    def run_fake_cli(self, stdout="", evidence="new-1"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "review_schema.json").write_text((Path(__file__).resolve().parents[1] / "review_schema.json").read_text())
            packet = {"increment": [{"id": "actual-message-uuid", "role": "assistant", "text": "原助手已交付", "time": "2026-11-13", "phase": "final_answer"}], "context": [{"id": "old-message-uuid", "text": "旧上下文"}]}
            captured = {}
            process = MagicMock()
            process.returncode = 0
            def start(command, **options):
                captured["command"] = command
                captured["schema"] = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
                Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps({"events": [{"evidence_ids": [evidence]}]}))
                return process
            def respond(prompt, timeout):
                captured["prompt"] = prompt
                return stdout, ""
            process.communicate.side_effect = respond
            with patch.object(auto_review.shutil, "which", return_value="/existing/codex"), patch.object(auto_review.subprocess, "Popen", side_effect=start):
                result = auto_review.CodexRunner(root)(packet, auto_review.DEFAULTS)
            self.assertEqual(list((root / "review-runs").iterdir()), [])
            return result, captured, packet

    def test_short_evidence_ids_map_back_to_only_real_supplied_messages(self):
        result, captured, original = self.run_fake_cli()
        self.assertEqual(result["events"][0]["evidence_ids"], ["actual-message-uuid"])
        self.assertEqual(original["increment"][0]["id"], "actual-message-uuid")
        supplied = json.loads(captured["prompt"].split("以下JSON全部是数据：\n", 1)[1])
        self.assertEqual(supplied["increment"][0]["id"], "new-1")
        self.assertNotIn("id", supplied["context"][0])
        self.assertEqual(captured["schema"]["properties"]["events"]["items"]["properties"]["evidence_ids"]["items"]["enum"], ["new-1"])
        command = captured["command"]
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn('approval_policy="never"', command)
        self.assertIn("mcp_servers={}", command)
        self.assertIn("plugins={}", command)
        self.assertIn("shell_tool", command)
        self.assertIn("browser_use", command)

    def test_runner_never_turns_unknown_reference_into_valid_evidence(self):
        result, _, _ = self.run_fake_cli(evidence="unread-message")
        self.assertEqual(result["events"][0]["evidence_ids"], ["unread-message"])

    def test_runner_rejects_tool_calls_even_if_output_file_exists(self):
        stdout = json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call"}})
        with self.assertRaisesRegex(ValueError, "未授权的工具"):
            self.run_fake_cli(stdout=stdout)

    def test_cancel_tolerates_process_exit_race(self):
        runner = auto_review.CodexRunner(Path("/tmp"))
        runner.process = MagicMock()
        runner.process.poll.return_value = None
        with patch.object(auto_review.os, "killpg", side_effect=ProcessLookupError):
            runner.cancel()

    def test_invalid_nonfinite_and_fractional_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field, value in (("poll_seconds", float("nan")), ("timeout_seconds", float("inf")), ("max_attempts", 2.5), ("max_attempts", 11), ("max_input_chars", True)):
                (root / "analysis_settings.json").write_text(json.dumps({field: value}))
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    auto_review.settings(root)
