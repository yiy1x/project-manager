import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from auto_review import validate_result
from next_steps import add_plans, text_hash, validate_plan
from demo import analysis


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.brief = {"summary": "真实调用与用户验收仍待完成", "points": [{"label": "验证", "text": "原助手自报通过，不等于独立核验。"}]}
        self.result = {"goal": "目标", "stage": "待验收", "progress": "交付", "blocker": "完整限制", "next": "下一步", "events": [], "next_plan": {"summary": "确认预览后再发布", "points": [{"label": "验收", "text": "不自动发布。"}]}, "confirmation_plan": self.brief}

    def test_current_presets_cover_all_tasks_with_bounded_briefs(self):
        for task in analysis()["tasks"]:
            self.assertEqual(validate_plan(task["confirmation_plan"], "待确认", 0), task["confirmation_plan"])

    def test_no_confirmations_does_not_require_invented_points(self):
        self.result["confirmation_plan"] = {"summary": "暂无明确待确认事项", "points": []}
        self.assertEqual(validate_result(self.result, [])["confirmation_plan"]["points"], [])
        self.result["next_plan"]["points"] = []
        with self.assertRaises(ValueError):
            validate_result(self.result, [])

    def test_invalid_confirmation_rejects_entire_model_result(self):
        malformed = [None, {}, {**self.brief, "summary": "字" * 41}, {**self.brief, "points": self.brief["points"] * 4}, {**self.brief, "points": [{"label": "超过四个字", "text": "内容"}]}, {**self.brief, "points": [{"label": "验证", "text": "字" * 49}]}, {**self.brief, "summary": " "}]
        for brief in malformed:
            with self.subTest(brief=brief), self.assertRaises(ValueError):
                validate_result({**self.result, "confirmation_plan": brief}, [])
        del self.result["confirmation_plan"]
        with self.assertRaises(ValueError):
            validate_result(self.result, [])

    def test_preset_invalidates_on_blocker_change_and_model_result_takes_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {"id": "example", "blocker": "完整限制", "needs_review": True, "next": "原下一步"}
            original = copy.deepcopy(task)
            (root / "confirmations.json").write_text(json.dumps({"tasks": {"example": {"blocker_hash": text_hash(task["blocker"]), "plan": self.brief}}}))
            add_plans(root, [task])
            self.assertEqual(task.pop("confirmation_plan"), self.brief)
            self.assertEqual(task, original)
            task["blocker"] = "真实验证已经补齐"
            add_plans(root, [task])
            self.assertNotIn("confirmation_plan", task)
            task["confirmation_plan"] = {"summary": "暂无明确待确认事项", "points": []}
            add_plans(root, [task])
            self.assertEqual(task["confirmation_plan"]["points"], [])
