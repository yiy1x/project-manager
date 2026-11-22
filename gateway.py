import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from sources import now, safe_text
from configuration import trace_url

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def route_snapshot(payload, clock=None):
    clock = clock if clock is not None else datetime.now().timestamp()
    if not isinstance(payload, dict) or payload.get("object") != "trace" or not isinstance(payload.get("routes"), list):
        raise ValueError("猫老大返回了不兼容的状态格式")
    observed = datetime.fromisoformat(payload["now"].replace("Z", "+00:00")).timestamp()
    if not -5 <= clock - observed <= 10:
        raise ValueError("猫老大状态快照已过期")
    routes = []
    kept = payload.get("ids")
    for route in payload["routes"]:
        if not isinstance(route, dict) or route.get("agent") not in ("claude", "codex") or route.get("kind"):
            continue
        if type(route.get("done")) is not bool or not isinstance(route.get("id"), (int, str)):
            continue
        if isinstance(kept, list) and route["id"] not in kept:
            continue
        session = route.get("session")
        if session is not None and not isinstance(session, str):
            continue
        status = route.get("status")
        status = status if type(status) is int else None
        if not route["done"]:
            kind, label = "running", "模型请求中"
        elif status is not None and status >= 400:
            kind, label = "error", "最近请求失败"
        elif status is not None and 200 <= status < 300:
            kind, label = "idle", "请求已结束"
        else:
            kind, label = "unknown", "请求已停止"
        routes.append({
            "id": str(route["id"]), "agent": route["agent"], "session": session or "",
            "source_id": route["agent"] + ":" + session if session else None,
            "done": route["done"], "kind": kind, "label": label, "http_status": status,
            "time": route.get("time") if isinstance(route.get("time"), str) else "",
            "model": safe_text(route.get("model"))[:100], "folder": safe_text(route.get("folder"))[:200],
        })
    changed = [str(route["id"]) for route in payload["routes"] if isinstance(route, dict) and isinstance(route.get("id"), (int, str))]
    return {"connected": True, "observed_at": payload["now"], "checked_at": now(), "routes": routes, "error": None, "seq": payload.get("seq"), "pid": payload.get("pid"), "ids": [str(identity) for identity in kept] if isinstance(kept, list) else None, "changed_ids": changed}


def read_gateway(after=None):
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        endpoint = trace_url()
        url = endpoint if after is None else endpoint + f"?after={int(after)}&wait=1"
        with opener.open(url, timeout=1.5 if after is None else 35) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("猫老大状态响应过大")
        return route_snapshot(json.loads(body))
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        reason = "需要授权，未读取凭据" if isinstance(error, HTTPError) and error.code in (401, 403) else "接口不可用或状态格式不兼容"
        return {"connected": False, "checked_at": now(), "routes": [], "error": reason}
