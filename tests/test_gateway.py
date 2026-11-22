import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activity import read_activity
from gateway import NoRedirect, read_gateway, route_snapshot


def payload(routes):
    return {"object": "trace", "now": datetime.now(timezone.utc).isoformat(), "routes": routes, "ids": [route["id"] for route in routes if isinstance(route, dict) and "id" in route]}


def route(identity=1, session="one", done=False, status=200, agent="claude", **extra):
    return {"id": identity, "session": session, "agent": agent, "done": done, "status": status, "time": datetime.now(timezone.utc).isoformat(), "model": "test-model", "folder": "test/project", **extra}


class GatewayTests(unittest.TestCase):
    def test_only_route_metadata_is_returned_and_internal_calls_are_excluded(self):
        data = payload([route(prompt="SECRET", responseId="PRIVATE", tries=[{"key": "SECRET"}]), route(2, kind="guardian"), route(3, kind="thread_title"), route(4, agent="other"), route(5, session=None), route(6, session={}), route(7, done="false")])
        result = route_snapshot(data)
        self.assertEqual(len(result["routes"]), 2)
        self.assertEqual(result["routes"][0]["source_id"], "claude:one")
        self.assertIsNone(result["routes"][1]["source_id"])
        for private in ("SECRET", "PRIVATE", "prompt", "responseId", "tries"):
            self.assertNotIn(private, json.dumps(result))

    def test_inflight_with_http_200_is_not_completed(self):
        result = route_snapshot(payload([route(), route(2, done=True), route(3, done=True, status=500), route(4, done=True, status=None)]))
        self.assertEqual([item["label"] for item in result["routes"]], ["模型请求中", "请求已结束", "最近请求失败", "请求已停止"])

    def test_old_snapshot_and_removed_ids_do_not_stay_running(self):
        data = payload([route()])
        data["ids"] = []
        self.assertEqual(route_snapshot(data)["routes"], [])
        data["now"] = "2000-01-01T00:00:00+00:00"
        with self.assertRaises(ValueError):
            route_snapshot(data)
        with self.assertRaises(ValueError):
            route_snapshot({"object": "wrong", "routes": []})

    def test_gateway_connection_failure_clears_live_routes_and_does_not_follow_redirects(self):
        with patch("gateway.build_opener") as build:
            build.return_value.open.side_effect = URLError("unavailable")
            self.assertEqual(read_gateway()["routes"], [])
            build.return_value.open.side_effect = HTTPError("local", 403, "Forbidden", {}, None)
            result = read_gateway()
            self.assertFalse(result["connected"])
            self.assertIn("未读取凭据", result["error"])
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://external.example"))

    def test_gateway_response_is_bounded(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"x" * (2 * 1024 * 1024 + 1)
        with patch("gateway.build_opener") as build:
            build.return_value.open.return_value = response
            self.assertFalse(read_gateway()["connected"])
            build.return_value.open.assert_called_once_with("http://127.0.0.1:3425/v1/catboss/trace", timeout=1.5)

    def test_incremental_reader_uses_cursor_and_wait_without_model_requests(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({**payload([]), "seq": 12, "pid": 20}).encode()
        with patch("gateway.build_opener") as build:
            build.return_value.open.return_value = response
            result = read_gateway(11)
            self.assertTrue(result["connected"])
            self.assertEqual((result["seq"], result["pid"]), (12, 20))
            build.return_value.open.assert_called_once_with("http://127.0.0.1:3425/v1/catboss/trace?after=11&wait=1", timeout=35)

    def test_supplied_subscription_snapshot_never_refetches_gateway(self):
        data = route_snapshot(payload([route(), route(2, agent="codex", session="tracked")]))
        with tempfile.TemporaryDirectory() as home, patch("activity.read_gateway") as fetch:
            result = read_activity({"codex:tracked"}, home, gateway_snapshot=data)
            fetch.assert_not_called()
        self.assertEqual(result["statuses"]["claude:one"]["kind"], "running")
        self.assertEqual(result["statuses"]["codex:tracked"]["kind"], "running")
        self.assertNotIn("ids", result["gateway"])

    def test_activity_matches_exact_session_and_allows_new_unsaved_claude_requests(self):
        data = route_snapshot(payload([route(), route(2, session="untracked", agent="codex"), route(3, session="tracked", agent="codex"), route(4, session=None)]))
        with tempfile.TemporaryDirectory() as home, patch("activity.read_gateway", return_value=data):
            result = read_activity({"codex:tracked"}, home)
        self.assertEqual(result["statuses"]["claude:one"]["label"], "模型请求中")
        self.assertEqual(result["statuses"]["codex:tracked"]["label"], "模型请求中")
        self.assertNotIn("codex:untracked", result["statuses"])
        self.assertNotIn("claude:", result["statuses"])
        self.assertEqual(len(result["live_requests"]), 2)

    def test_active_request_wins_until_all_concurrent_requests_finish(self):
        with tempfile.TemporaryDirectory() as home:
            data = route_snapshot(payload([route(), route(2, done=True)]))
            with patch("activity.read_gateway", return_value=data):
                self.assertEqual(read_activity(set(), home)["statuses"]["claude:one"]["label"], "模型请求中")
            data = route_snapshot(payload([route(done=True), route(2, done=True)]))
            with patch("activity.read_gateway", return_value=data):
                result = read_activity(set(), home)
                self.assertEqual(result["statuses"]["claude:one"]["label"], "请求已结束")
                self.assertEqual(result["live_requests"], [])
            with patch("activity.read_gateway", return_value=route_snapshot(payload([]))):
                self.assertEqual(read_activity(set(), home)["statuses"], {})

    def test_long_running_request_does_not_expire_based_on_start_time(self):
        data = route_snapshot(payload([route(time="2000-01-01T00:00:00+00:00")]))
        with tempfile.TemporaryDirectory() as home, patch("activity.read_gateway", return_value=data):
            self.assertEqual(read_activity(set(), home)["statuses"]["claude:one"]["kind"], "running")


if __name__ == "__main__":
    unittest.main()
