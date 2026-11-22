import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from next_steps import add_plans, text_hash, validate_plan
from auto_review import validate_result
from demo import analysis


class NextStepTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"summary": "先确认范围，再验证方案", "points": [{"label": "确认", "text": "确认授权范围，不自动发送资料。"}]}

    def test_all_curated_plans_have_short_summaries_and_action_points(self):
        for task in analysis()["tasks"]:
            self.assertEqual(validate_plan(task["next_plan"]), task["next_plan"])

    def test_model_must_return_valid_plan_and_cannot_accumulate_long_points(self):
        result = {"goal": "目标", "stage": "待验收", "progress": "已交付", "blocker": "待确认", "next": "详细原说明", "events": [], "next_plan": self.plan}
        result["confirmation_plan"] = {"summary": "暂无明确待确认事项", "points": []}
        self.assertEqual(validate_result(result, [])["next_plan"], self.plan)
        malformed = [None, {}, {**self.plan, "summary": "字" * 41}, {**self.plan, "points": []}, {**self.plan, "points": self.plan["points"] * 4}, {**self.plan, "points": [{"label": "过长标签名", "text": "内容"}]}, {**self.plan, "points": [{"label": "验证", "text": "字" * 49}]}, {**self.plan, "summary": " "}]
        for plan in malformed:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                validate_result({**result, "next_plan": plan}, [])
        del result["next_plan"]
        with self.assertRaises(ValueError):
            validate_result(result, [])

    def test_presets_match_exact_raw_text_and_do_not_change_analysis_or_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {"id": "example", "next": "保留的详细下一步", "source_ids": ["codex:one"], "needs_review": True}
            original = copy.deepcopy(task)
            (root / "next_steps.json").write_text(json.dumps({"tasks": {"example": {"next_hash": text_hash(task["next"]), "plan": self.plan}}}))
            add_plans(root, [task])
            self.assertEqual(task.pop("next_plan"), self.plan)
            self.assertEqual(task, original)
            task["next"] = "新一轮已经更新的下一步"
            add_plans(root, [task])
            self.assertNotIn("next_plan", task)

    def test_new_model_plan_wins_and_legacy_or_invalid_plan_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = {"summary": "验收新一轮成果", "points": [{"label": "检查", "text": "不再使用上一轮的待办。"}]}
            task = {"id": "example", "next": "原文", "next_plan": model}
            (root / "next_steps.json").write_text(json.dumps({"tasks": {"example": {"next_hash": text_hash("原文"), "plan": self.plan}}}))
            add_plans(root, [task])
            self.assertEqual(task["next_plan"], model)
            task["next_plan"] = {"summary": "无行动要点"}
            add_plans(root, [task])
            self.assertNotIn("next_plan", task)
            legacy = {"id": "old", "next": "旧的长说明"}
            add_plans(root, [legacy])
            self.assertEqual(legacy, {"id": "old", "next": "旧的长说明"})
