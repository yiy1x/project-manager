import queue
import threading
import time

from gateway import read_gateway
from sources import now


def disconnected(reason):
    return {"connected": False, "checked_at": now(), "routes": [], "error": reason}


class RouteState:
    def __init__(self):
        self.routes = {}
        self.seq = None
        self.pid = None

    def merge(self, delta):
        if not delta["connected"]:
            self.routes.clear()
            self.seq = self.pid = None
            return delta
        if type(delta.get("seq")) is not int or type(delta.get("pid")) is not int or not isinstance(delta.get("ids"), list):
            return self.merge(disconnected("猫老大未提供可用的增量游标"))
        if self.seq is not None and (delta["pid"] != self.pid or delta["seq"] < self.seq):
            return self.merge(disconnected("猫老大已重启，重新同步快照"))
        self.seq, self.pid = delta["seq"], delta["pid"]
        for identity in delta.get("changed_ids", []):
            self.routes.pop(identity, None)
        self.routes.update((route["id"], route) for route in delta["routes"])
        kept = set(delta["ids"])
        self.routes = {identity: route for identity, route in self.routes.items() if identity in kept}
        return {**delta, "routes": list(self.routes.values())}


class RouteFeed:
    def __init__(self, reader=read_gateway):
        self.reader = reader
        self.condition = threading.Condition()
        self.subscribers = set()
        self.thread = None
        self.closed = False
        self.snapshot = disconnected("正在订阅猫老大")
        self.received_at = 0

    def subscribe(self):
        channel = queue.Queue(maxsize=32)
        with self.condition:
            if self.closed:
                raise RuntimeError("状态服务已关闭")
            self.subscribers.add(channel)
            channel.put(self.current())
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
            self.condition.notify_all()
        return channel

    def unsubscribe(self, channel):
        with self.condition:
            self.subscribers.discard(channel)
            if not self.subscribers:
                self.snapshot = disconnected("页面已离开，订阅暂停")
                self.received_at = 0
            self.condition.notify_all()

    def current(self):
        with self.condition:
            if self.snapshot["connected"] and time.monotonic() - self.received_at > 40:
                return disconnected("猫老大增量连接超时")
            return self.snapshot

    def publish(self, snapshot):
        with self.condition:
            self.snapshot = snapshot
            self.received_at = time.monotonic()
            for channel in self.subscribers:
                try:
                    channel.put_nowait(snapshot)
                except queue.Full:
                    try:
                        while True:
                            channel.get_nowait()
                    except queue.Empty:
                        pass
                    channel.put_nowait(snapshot)

    def run(self):
        state = RouteState()
        previous = None
        failures = 0
        while True:
            with self.condition:
                if self.closed or not self.subscribers:
                    self.thread = None
                    return
            started = time.monotonic()
            try:
                delta = self.reader(state.seq)
                snapshot = state.merge(delta)
            except (OSError, ValueError, TypeError, KeyError):
                snapshot = state.merge(disconnected("猫老大订阅失败"))
            signature = (snapshot["connected"], snapshot.get("error"), snapshot["routes"])
            with self.condition:
                if self.closed or not self.subscribers:
                    self.thread = None
                    return
                if signature != previous:
                    self.publish(snapshot)
                    previous = signature
                else:
                    self.snapshot = snapshot
                    self.received_at = time.monotonic()
                if snapshot["connected"]:
                    failures = 0
                    delay = max(0, 0.25 - (time.monotonic() - started))
                else:
                    failures += 1
                    delay = min(30, 2 ** min(failures, 5))
                self.condition.wait(timeout=delay)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()


FEED = RouteFeed()
