import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import route_snapshot
from live import RouteFeed, RouteState, disconnected
from test_gateway import payload, route


def delta(routes, seq=1, pid=10, ids=None):
    data = {**payload(routes), "seq": seq, "pid": pid}
    if ids is not None:
        data["ids"] = ids
    return route_snapshot(data)


class RouteStateTests(unittest.TestCase):
    def test_delta_retains_unchanged_requests_and_removes_evicted_ids(self):
        state = RouteState()
        state.merge(delta([route(), route(2, agent="codex")]))
        result = state.merge(delta([route(done=True)], seq=2, ids=[1, 2]))
        self.assertEqual(len(result["routes"]), 2)
        self.assertTrue(state.routes["1"]["done"])
        self.assertFalse(state.routes["2"]["done"])
        result = state.merge(delta([], seq=3, ids=[1]))
        self.assertEqual([item["id"] for item in result["routes"]], ["1"])

    def test_unchanged_response_keeps_snapshot_but_updates_freshness(self):
        state = RouteState()
        state.merge(delta([route()]))
        heartbeat = delta([], ids=[1])
        result = state.merge(heartbeat)
        self.assertEqual(len(result["routes"]), 1)
        self.assertEqual(result["observed_at"], heartbeat["observed_at"])

    def test_reclassified_request_does_not_keep_old_running_status(self):
        state = RouteState()
        state.merge(delta([route()]))
        result = state.merge(delta([route(kind="thread_title")], seq=2))
        self.assertEqual(result["routes"], [])

    def test_restart_or_cursor_regression_requires_full_resync(self):
        for changed in ({"pid": 11, "seq": 2}, {"seq": 0}):
            state = RouteState()
            state.merge(delta([route()]))
            result = state.merge(delta([], **changed))
            self.assertFalse(result["connected"])
            self.assertEqual(result["routes"], [])
            self.assertIsNone(state.seq)
            result = state.merge(delta([route(2)], **changed))
            self.assertTrue(result["connected"])
            self.assertEqual([item["id"] for item in result["routes"]], ["2"])

    def test_disconnection_and_invalid_cursor_clear_running_requests(self):
        for broken in (disconnected("offline"), {**delta([]), "seq": None}, {**delta([]), "pid": True}, {**delta([]), "ids": None}):
            state = RouteState()
            state.merge(delta([route()]))
            result = state.merge(broken)
            self.assertFalse(result["connected"])
            self.assertEqual(state.routes, {})
            self.assertIsNone(state.seq)


class RouteFeedTests(unittest.TestCase):
    def test_multiple_pages_share_one_upstream_and_close_stops_worker(self):
        incoming = queue.Queue()
        calls = queue.Queue()

        def reader(after):
            calls.put(after)
            return incoming.get(timeout=3)

        feed = RouteFeed(reader)
        first = feed.subscribe()
        worker = feed.thread

        def cleanup():
            feed.close()
            incoming.put(disconnected("stop"))
            worker.join(timeout=4)

        self.addCleanup(cleanup)
        self.assertFalse(first.get(timeout=2)["connected"])
        self.assertIsNone(calls.get(timeout=2))
        second = feed.subscribe()
        self.assertIs(feed.thread, worker)
        self.assertFalse(second.get(timeout=2)["connected"])
        incoming.put(delta([route()]))
        self.assertTrue(first.get(timeout=2)["connected"])
        self.assertTrue(second.get(timeout=2)["connected"])
        self.assertEqual(calls.get(timeout=2), 1)
        incoming.put(delta([route(done=True)], seq=2))
        self.assertTrue(first.get(timeout=2)["routes"][0]["done"])
        self.assertTrue(second.get(timeout=2)["routes"][0]["done"])
        feed.unsubscribe(first)
        feed.unsubscribe(second)
        self.assertFalse(feed.current()["connected"])
        cleanup()
        self.assertFalse(worker.is_alive())
        with self.assertRaises(RuntimeError):
            feed.subscribe()

    def test_slow_subscriber_is_bounded_and_keeps_latest_snapshot(self):
        feed = RouteFeed()
        channel = queue.Queue(maxsize=2)
        feed.subscribers.add(channel)
        for sequence in range(1, 5):
            feed.publish(delta([route(done=sequence == 4)], seq=sequence))
        self.assertLessEqual(channel.qsize(), 2)
        snapshots = []
        while not channel.empty():
            snapshots.append(channel.get_nowait())
        self.assertEqual(snapshots[-1]["seq"], 4)
        self.assertTrue(snapshots[-1]["routes"][0]["done"])

    def test_stale_upstream_cannot_be_presented_as_live(self):
        feed = RouteFeed()
        feed.publish(delta([route()]))
        with patch("live.time.monotonic", return_value=time.monotonic() + 41):
            result = feed.current()
        self.assertFalse(result["connected"])
        self.assertEqual(result["routes"], [])

    def test_reader_failure_drops_routes_without_busy_retrying(self):
        called = threading.Event()

        def reader(after):
            called.set()
            raise OSError("offline")

        feed = RouteFeed(reader)
        channel = feed.subscribe()
        worker = feed.thread
        try:
            channel.get(timeout=2)
            self.assertTrue(called.wait(2))
            failed = channel.get(timeout=2)
            self.assertFalse(failed["connected"])
            self.assertEqual(failed["routes"], [])
            with self.assertRaises(queue.Empty):
                channel.get(timeout=0.1)
        finally:
            feed.close()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
