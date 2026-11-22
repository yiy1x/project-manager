import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ASSETS = Path(__file__).resolve().parent


def source_home(home=None):
    return Path(home or os.environ.get("MANAGER_SOURCE_HOME") or Path.home()).expanduser().resolve()


def trace_url():
    value = os.environ.get("CATBOSS_TRACE_URL", "http://127.0.0.1:3425/v1/catboss/trace")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("CATBOSS_TRACE_URL 必须是不含凭据和查询参数的本机 HTTP 地址")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("猫老大端口不合法")
    return value


def write_new(path, payload):
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        path.chmod(0o600)
    except FileExistsError:
        pass


def initialize_data(root, demo=False):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = {"mode": "demo" if demo else "local"}
    marker = root / "instance.json"
    if marker.exists() and json.loads(marker.read_text()) != mode:
        raise ValueError("演示和真实数据不能共用目录，请另选 --data-dir")
    if demo and not marker.exists() and any(root.iterdir()):
        raise ValueError("演示只能初始化空目录，不能覆盖已有数据")
    write_new(marker, mode)
    if demo:
        from demo import analysis, history
        initial = analysis()
        events = history()
    else:
        initial = {"tasks": [], "analysis_date": "尚未分析", "analysis_method": "扫描不等于语义分析。手动回读或主动启用自动分析后更新。", "highlights": []}
        events = {"events": []}
    write_new(root / "analysis.json", initial)
    write_new(root / "timeline.json", events)
    write_new(root / "tracking.json", {"mode": "allowlist", "source_ids": [identity for task in initial["tasks"] for identity in task["source_ids"]]})
    write_new(root / "analysis_settings.json", {"enabled": False})
