import argparse
import json
import queue
import secrets
import signal
import sqlite3
import threading
import time
import tempfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from activity import read_activity
from catalog import search_projects
from live import FEED
from next_steps import add_plans
from timeline import for_task, load_history, save_manual
import auto_review

from sources import collect, now, safe_text
from configuration import ASSETS, initialize_data
import demo

ROOT = ASSETS / "data"
DATABASE = ROOT / "总管.sqlite3"
LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(32)
LAST_SCAN = 0.0
REVIEWER = None
DEMO = False


@contextmanager
def database():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    initialize_data(ROOT, DEMO)
    with database() as connection:
        connection.executescript("CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, queue TEXT NOT NULL DEFAULT '跟进中'); CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, text TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, task_id TEXT, action TEXT NOT NULL, created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS baselines (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);")
        connection.execute("CREATE TABLE IF NOT EXISTS custom_tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS milestones (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, payload TEXT NOT NULL)")
        auto_review.initialize(connection)
    DATABASE.chmod(0o600)


def allowed_sources():
    path = ROOT / "tracking.json"
    if not path.exists():
        raise ValueError("缺少跟进白名单，停止扫描")
    configuration = json.loads(path.read_text())
    if not isinstance(configuration, dict) or configuration.get("mode") != "allowlist" or not isinstance(configuration.get("source_ids"), list) or not all(isinstance(identity, str) and identity for identity in configuration["source_ids"]):
        raise ValueError("跟进范围配置不合法，停止扫描")
    return set(configuration["source_ids"])


def refresh(force=False):
    global LAST_SCAN
    with LOCK:
        if not force and time.monotonic() - LAST_SCAN < 15:
            return
        allowed = allowed_sources()
        snapshot = demo.snapshot() if DEMO else collect(allowed_ids=allowed)
        with database() as connection:
            old = connection.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()
            if old:
                present = {entry["id"]: entry for entry in snapshot["entries"]}
                for entry in json.loads(old[0])["entries"]:
                    if allowed is not None and entry["id"] not in allowed:
                        continue
                    current = present.get(entry["id"])
                    if current is None or (entry["category"] == "聊天" and current["category"] != "聊天"):
                        if current:
                            entry.update({key: current[key] for key in ("route", "route_evidence") if key in current})
                        entry["source_stale"] = True
                        entry.setdefault("missing_since_scan", snapshot["scanned_at"])
                        present[entry["id"]] = entry
                snapshot["entries"] = list(present.values())
            snapshot["statistics"]["inventory_entries"] = len(snapshot["entries"])
            snapshot["statistics"]["chat_entries"] = sum(entry["category"] == "聊天" for entry in snapshot["entries"])
            connection.execute("INSERT OR REPLACE INTO snapshot VALUES (1,?)", (json.dumps(snapshot, ensure_ascii=False),))
            for entry in snapshot["entries"]:
                connection.execute("INSERT OR IGNORE INTO baselines VALUES (?,?)", (entry["id"], entry.get("fingerprint", "")))
        LAST_SCAN = time.monotonic()


def state():
    refresh()
    analyses = json.loads((ROOT / "analysis.json").read_text())
    with database() as connection:
        connection.execute("BEGIN")
        snapshot = json.loads(connection.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()[0])
        queues = {row["id"]: row["queue"] for row in connection.execute("SELECT * FROM tasks")}
        baselines = {row["id"]: row["fingerprint"] for row in connection.execute("SELECT * FROM baselines")}
        notes = [dict(row) for row in connection.execute("SELECT * FROM notes ORDER BY pinned DESC, id DESC")]
        analyses["tasks"].extend(json.loads(row[0]) for row in connection.execute("SELECT payload FROM custom_tasks"))
        auto_review.overlays(connection, analyses["tasks"])
        add_plans(ROOT, analyses["tasks"])
        history = load_history(ROOT, connection)
        automatic = auto_review.settings(ROOT)
        revision = connection.execute("SELECT value FROM review_meta WHERE id='revision'").fetchone()[0]
        last_success = connection.execute("SELECT MAX(finished_at) FROM review_jobs WHERE status='completed'").fetchone()[0] or ""
    allowed = allowed_sources()
    if allowed is not None:
        snapshot["entries"] = [entry for entry in snapshot["entries"] if entry["id"] in allowed]
        snapshot["statistics"]["inventory_entries"] = len(snapshot["entries"])
        snapshot["statistics"]["chat_entries"] = sum(entry["category"] == "聊天" for entry in snapshot["entries"])
        for task in analyses["tasks"]:
            task["source_ids"] = [identity for identity in task["source_ids"] if identity in allowed]
        analyses["tasks"] = [task for task in analyses["tasks"] if task["source_ids"]]
    entries = {entry["id"]: entry for entry in snapshot["entries"]}
    for task in analyses["tasks"]:
        task["queue"] = queues.get(task["id"], "跟进中")
        task["notes"] = [note for note in notes if note["task_id"] == task["id"]]
        task["sources"] = [entries[identity] for identity in task["source_ids"] if identity in entries]
        task["timeline"] = for_task(history, task)
        task["needs_review"] = task["id"] != "manager" and any(entry.get("fingerprint") != baselines.get(entry["id"]) for entry in task["sources"])
        with database() as connection:
            task["auto_analysis"] = auto_review.review_state(connection, task, task["sources"], automatic["enabled"])
    return {**snapshot, **analyses, "demo": DEMO, "auto_review": {"enabled": automatic["enabled"] and not DEMO, "revision": revision, "last_success_at": last_success, "checked_at": REVIEWER.last_checked_at if REVIEWER else "", "error": REVIEWER.error if REVIEWER else ""}, "token": TOKEN}


def export_bundle():
    with LOCK:
        refresh(True)
        data = state()
        data.pop("token", None)
        (ROOT / "全量台账.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        export_report(data)
    FEED.publish(FEED.current())


def set_auto_review(enabled):
    if DEMO and enabled:
        raise ValueError("演示模式禁止模型调用；请使用独立的真实数据目录")
    options = auto_review.settings(ROOT)
    options["enabled"] = enabled
    with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, prefix=".analysis-settings-", delete=False) as stream:
        json.dump(options, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.replace(ROOT / "analysis_settings.json")
    with database() as connection:
        connection.execute("UPDATE review_meta SET value=value+1 WHERE id='revision'")
    if REVIEWER:
        if not enabled:
            REVIEWER.runner.cancel()
        REVIEWER.wake.set()
    FEED.publish(FEED.current())


def export_report(data):
    statistics = data["statistics"]
    discovery = "1. 看板打开时，每15秒只刷新tracking.json指定的聊天，其他聊天不再自动导入。" if allowed_sources() is not None else "1. 看板打开时，每15秒读取本机新记录，不发模型请求。新聊天进入全量台账，可点击“加入跟进”。"
    analysis_status = f"人工分析基线：{data['analysis_date']}；最近自动回读成功：{data.get('auto_review', {}).get('last_success_at') or '暂无'}。各任务已读位置独立记录，扫描不等于语义分析。"
    lines = ["# 项目总管：首轮盘点与切回指南", "", f"记录同步时间：{data['scanned_at']}（北京时间）", analysis_status, "", "## 覆盖范围", "", data["scope"], "", f"本次台账共 {len(data['entries'])} 个去重条目，其中 {statistics['chat_entries']} 条有正文的主聊天，整理为 {len(data['tasks'])} 项逻辑任务（含总管自身）。Codex 索引 {statistics['codex_threads']} 条；Claude 原始文件 {statistics['claude_files']} 个；已合并导入副本 {statistics['import_duplicates']} 个；网关片段 {statistics['gateway_files']} 份。", "", "路由留存日期：" + "、".join(statistics["route_dates"]), "", data["analysis_method"], "", "不将系统审批/子代理、自动标题、测试探针、未绑定调用冒充独立业务任务。历史 Claude 导入记录纳入关联台账，但不能据此声称原始请求一定走过猫老大。", "", "## 简单分析", ""]
    lines.extend("- " + text for text in data["highlights"])
    lines.extend(["", "## 当前任务", "", "| 任务 | 优先级 | 实际阶段 | 下一步 |", "|---|---|---|---|"])
    for task in data["tasks"]:
        next_summary = task.get("next_plan", {}).get("summary", "详见逐任务切回卡")
        lines.append(f"| {task['name']} | {task['priority']} | {task['stage']} | {next_summary} |")
    lines.extend(["", "## 逐任务切回卡", ""])
    for task in data["tasks"]:
        lines.extend([f"### {task['name']}", "", f"- 归属：{task['project']}；队列：{task['queue']}；证据等级：{task['confidence']}", f"- 目标：{task['goal']}", f"- 当前进展：{task['progress']}"])
        if task.get("next_plan"):
            lines.extend(["", "**" + task["next_plan"]["summary"] + "**"])
            lines.extend("- **" + point["label"] + "：**" + point["text"] for point in task["next_plan"]["points"])
        lines.extend(["", "<details><summary>完整下一步说明</summary>", "", task["next"], "", "</details>", ""])
        if task.get("confirmation_plan"):
            lines.extend(["**待确认：" + task["confirmation_plan"]["summary"] + "**"])
            lines.extend("- **" + point["label"] + "：**" + point["text"] for point in task["confirmation_plan"]["points"])
        lines.extend(["", "<details><summary>完整待确认说明</summary>", "", task["blocker"], "", "</details>", ""])
        if task["needs_review"]:
            lines.append("- **有新消息，语义摘要待复核。**")
        if task.get("auto_analysis"):
            lines.append(f"- 已分析至：{task['auto_analysis']['reviewed_through'] or '暂无'}；{task['auto_analysis']['label']}。")
        lines.extend("- 依据：" + text for text in task["evidence"])
        lines.extend("- 注意：" + text for text in task["cautions"])
        for branch in task.get("planned_branches", []):
            lines.append(f"- 预留分支：**{branch['name']}**；{branch['focus']} 等待用户创建聊天，尚未关联，不计入任务数。")
        for source in task["sources"]:
            lines.append(f"- 原聊天：{source['title']}；稳定ID：`{source['id']}`；最近记录：{source.get('updated', '未知')}")
        lines.extend(["", "#### 阶段时间线（最近在前）", ""])
        for event in task.get("timeline", []):
            origin = {"review": "对话回读", "auto": "自动回读", "manual": "手动补记", "queue": "管理操作"}[event["origin"]]
            lines.append(f"- **{event.get('summary') or event['title']}** · {event['occurred_at']} · {event['kind']} · {event.get('focus') or '未分类'}（{origin}）")
            lines.extend(f"  - **{point['label']}：**{point['text']}" for point in event.get("points", []))
            lines.append(f"  - 原文：{event['detail']}")
            lines.extend(f"  - 依据：`{reference['source_id']}` / 消息序号 {reference['ordinal']}：{reference['text']}" for reference in event["evidence"])
        lines.append("")
    lines.extend(["## 全量条目清单", "", "所有条目均保留在本地 SQLite 和全量台账 JSON 中；辅助调用不等于业务聊天。", "", "| 原始标题 | 类别 | 稳定 ID | 路由证据 |", "|---|---|---|---|"])
    for entry in data["entries"]:
        title = entry["title"].replace("|", " / ").replace("\n", " ")
        lines.append(f"| {title} | {entry['category']} | `{entry['id']}` | {entry.get('route_evidence', '未确认')} |")
    analysis_mode = "2. 已开启自动分析：跟进中的聊天整轮结束后，后台调用现有Codex回读新增对话，更新进度与阶段时间线；总管自身和暂停/已完成任务不自动分析。" if data.get("auto_review", {}).get("enabled") else "2. 自动分析已暂停。可在页脚开启，或在总管聊天说“刷新总管”进行人工回读；只刷新网页不会生成语义分析。"
    lines.extend(["", "## 如何继续使用", "", discovery, analysis_mode, "3. 提醒可多条追加、置顶、标为已处理；只有用户结项后才进入已完成，可恢复。", "4. 状态检测不发模型请求；自动分析会产生真实模型调用，不给原聊天发送指令、不修改原项目或模型配置，也未安装开机自启。", "5. ChatGPT账号历史、其他设备、本机未留存的网关正文、MLP当前作业状态都不在本轮已确认覆盖范围。", ""])
    (ROOT / "盘点报告.md").write_text("\n".join(lines))


class Handler(BaseHTTPRequestHandler):
    def valid_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def send(self, payload, status=200, mime="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_host():
            self.send({"error": "非法主机"}, 403)
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                self.send(state())
            elif path == "/api/activity/events":
                self.stream_activity()
            elif path == "/api/activity":
                self.send(current_activity())
            elif path == "/api/claude-projects":
                query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                if len(query) > 300:
                    self.send({"error": "搜索词最多300字"}, 400)
                    return
                self.send(demo.catalog() if DEMO else search_projects(query))
            elif path in ("/", "/style.css", "/ui.js", "/view-state.js"):
                filename, mime = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"), "/ui.js": ("ui.js", "text/javascript; charset=utf-8"), "/view-state.js": ("view-state.js", "text/javascript; charset=utf-8")}[path]
                self.send((ASSETS / filename).read_bytes(), mime=mime)
            else:
                self.send({"error": "不存在"}, 404)
        except (OSError, sqlite3.Error, ValueError) as error:
            self.send({"error": safe_text(error)}, 500)

    def stream_activity(self):
        origin = self.headers.get("Origin")
        if self.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}):
            self.send({"error": "请求来源校验失败"}, 403)
            return
        feed = FEED
        channel = feed.subscribe()
        try:
            self.connection.settimeout(15)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while not feed.closed:
                try:
                    snapshot = channel.get(timeout=10)
                except queue.Empty:
                    snapshot = feed.current()
                result = current_activity(snapshot)
                result["transport"] = "catboss-subscription"
                with database() as connection:
                    result["analysis_revision"] = connection.execute("SELECT value FROM review_meta WHERE id='revision'").fetchone()[0]
                message = "event: activity\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
                self.wfile.write(message.encode())
                self.wfile.flush()
        except (OSError, ValueError, sqlite3.Error):
            pass
        finally:
            feed.unsubscribe(channel)
            self.close_connection = True

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self.valid_host() or self.headers.get("X-Manager-Token") != TOKEN or (origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}):
            self.send({"error": "请求来源校验失败"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 20000:
                raise ValueError("请求大小不合法")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求正文必须是 JSON 对象")
            if self.path not in ("/api/refresh", "/api/add-task", "/api/queue", "/api/notes", "/api/note-state", "/api/timeline", "/api/auto-review", "/api/review-retry"):
                raise ValueError("未知操作")
            with LOCK:
                if self.path == "/api/auto-review":
                    if type(body.get("enabled")) is not bool:
                        raise ValueError("自动分析开关必须为布尔值")
                    set_auto_review(body["enabled"])
                    self.send({"ok": True})
                    return
                if self.path == "/api/refresh":
                    refresh(True)
                    self.send({"ok": True})
                    return
                current = state()
                if self.path == "/api/add-task":
                    entry = next((entry for entry in current["entries"] if entry["id"] == body.get("entry_id")), None)
                    if not entry or entry["category"] != "聊天":
                        raise ValueError("只能将有正文的聊天加入跟进")
                    if not any(entry["id"] in task["source_ids"] for task in current["tasks"]):
                        identity = "manual:" + entry["id"]
                        task = {"id": identity, "project": "待分析", "name": entry["title"], "priority": "P2", "stage": "已加入，等待语义分析", "confidence": "仅原始记录摘录，尚未由总管分析", "source_ids": [entry["id"]], "goal": entry.get("latest_request") or "待确认目标", "progress": "已登记原聊天。请在总管聊天中要求分析该任务；自动扫描不生成伪装成分析的摘要。", "blocker": "尚未分析", "next": "回总管聊天说：分析新加入的任务。", "evidence": ["按稳定源ID登记"], "cautions": ["最新请求摘录不等于整个聊天的目标。"]}
                        with database() as connection:
                            connection.execute("INSERT OR IGNORE INTO custom_tasks VALUES (?,?)", (identity, json.dumps(task, ensure_ascii=False)))
                    self.send({"ok": True})
                    return
                identity = body.get("task_id")
                task = next((task for task in current["tasks"] if task["id"] == identity), None)
                if not task:
                    raise ValueError("未知任务")
                with database() as connection:
                    if self.path == "/api/review-retry":
                        if not current["auto_review"]["enabled"] or task["queue"] != "跟进中" or identity == "manager":
                            raise ValueError("仅自动分析已开启的跟进任务可以重试")
                        connection.execute("UPDATE review_jobs SET status='queued',attempts=0,error=NULL,available_at=? WHERE task_id=? AND status IN ('retry','failed')", (time.time(), identity))
                        connection.execute("UPDATE review_meta SET value=value+1 WHERE id='revision'")
                        if REVIEWER:
                            REVIEWER.wake.set()
                    elif self.path == "/api/timeline":
                        save_manual(connection, body, identity)
                    elif self.path == "/api/queue":
                        queue = body.get("queue")
                        if queue not in ("跟进中", "已完成", "暂不跟进"):
                            raise ValueError("未知队列")
                        if queue != task["queue"]:
                            connection.execute("INSERT INTO tasks(id,queue) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET queue=excluded.queue", (identity, queue))
                            connection.execute("INSERT INTO events(task_id,action,created_at) VALUES (?,?,?)", (identity, queue, now()))
                    elif self.path == "/api/notes":
                        if not isinstance(body.get("text"), str):
                            raise ValueError("提醒必须是文字")
                        text = safe_text(body["text"]).strip()
                        if not text or len(text) > 3000:
                            raise ValueError("提醒应为 1–3000 字")
                        connection.execute("INSERT INTO notes(task_id,text,created_at) VALUES (?,?,?)", (identity, text, now()))
                    elif self.path == "/api/note-state":
                        field = body.get("field")
                        if field not in ("done", "pinned") or type(body.get("value")) is not bool or type(body.get("note_id")) is not int or body["note_id"] <= 0:
                            raise ValueError("未知提醒操作")
                        cursor = connection.execute(f"UPDATE notes SET {field}=? WHERE id=? AND task_id=?", (int(body["value"]), body["note_id"], identity))
                        if cursor.rowcount != 1:
                            raise ValueError("提醒不存在或不属于此任务")
            self.send({"ok": True})
        except (ValueError, OSError, sqlite3.Error) as error:
            self.send({"error": safe_text(error)}, 400)

    def log_message(self, format, *arguments):
        return


def main():
    global REVIEWER, ROOT, DATABASE, DEMO, FEED
    parser = argparse.ArgumentParser(description="只读扫描原始聊天，仅写本地总管库")
    parser.add_argument("command", choices=("serve", "scan", "export", "discover", "track"), nargs="?", default="serve")
    parser.add_argument("source_ids", nargs="*", help="track 命令要加入的 codex:ID 或 claude:ID")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, help="独立的本地数据目录，默认 ./data")
    parser.add_argument("--demo", action="store_true", help="虚构演示；完全不读取本机来源或调用模型")
    arguments = parser.parse_args()
    if arguments.source_ids and arguments.command != "track":
        parser.error("仅 track 命令接受聊天 ID")
    if arguments.demo and arguments.command in ("discover", "track"):
        parser.error("演示模式禁止扫描或添加真实聊天")
    DEMO = arguments.demo
    ROOT = (arguments.data_dir or ASSETS / ("demo-data" if DEMO else "data")).expanduser().resolve()
    DATABASE = ROOT / "总管.sqlite3"
    if DEMO:
        from live import RouteFeed, disconnected
        FEED = RouteFeed(reader=lambda after: disconnected("演示模式"))
    if arguments.command == "discover":
        discovery = collect()
        print(json.dumps({"chats": [{"id": entry["id"], "title": entry["title"], "source": entry["source"]} for entry in discovery["entries"] if entry["category"] == "聊天"], "errors": discovery["errors"]}, ensure_ascii=False, indent=2))
        return
    initialize()
    if arguments.command == "track":
        if not arguments.source_ids:
            parser.error("track 至少需要一个聊天 ID；先运行 discover 查看")
        track_sources(arguments.source_ids)
        print("已加入跟进；尚未生成语义分析。自动分析默认关闭。")
        return
    refresh(True)
    if arguments.command == "scan":
        data = state()
        print(json.dumps({"statistics": data["statistics"], "errors": data["errors"]}, ensure_ascii=False, indent=2))
    elif arguments.command == "export":
        data = state()
        data.pop("token", None)
        (ROOT / "全量台账.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        export_report(data)
        print("已导出全量台账.json 和 盘点报告.md")
    else:
        server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
        if not DEMO:
            REVIEWER = auto_review.ReviewService(ROOT, DATABASE, LOCK, FEED, on_update=export_bundle)
            REVIEWER.start()
        signal.signal(signal.SIGTERM, stop_serving)
        print(f"项目总管：http://127.0.0.1:{arguments.port}（Ctrl+C 停止；仅监听本机）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if REVIEWER:
                REVIEWER.close()
            FEED.close()
            server.server_close()


def stop_serving(signal_number, frame):
    raise KeyboardInterrupt


def current_activity(snapshot=None):
    return demo.activity() if DEMO else read_activity(allowed_sources() or [], gateway_snapshot=FEED.current() if snapshot is None else snapshot)


def track_sources(identities):
    if any(not identity.startswith(("codex:", "claude:")) for identity in identities):
        raise ValueError("聊天 ID 必须以 codex: 或 claude: 开头")
    discovered = collect(allowed_ids=set(identities))
    entries = {entry["id"]: entry for entry in discovered["entries"] if entry["category"] == "聊天"}
    if set(identities) - entries.keys():
        raise ValueError("未找到这些本机聊天：" + "、".join(sorted(set(identities) - entries.keys())))
    with database() as connection:
        known = json.loads((ROOT / "analysis.json").read_text())["tasks"]
        known.extend(json.loads(row[0]) for row in connection.execute("SELECT payload FROM custom_tasks"))
        for identity in dict.fromkeys(identities):
            if any(identity in task["source_ids"] for task in known):
                continue
            entry = entries[identity]
            task = {"id": "manual:" + identity, "name": entry["title"], "project": "待分析", "source_ids": [identity], "priority": "P2", "stage": "已加入，等待分析", "confidence": "仅登记，尚未语义分析", "goal": "待回读确认目标", "progress": "已登记本机聊天，不代表完成分析。", "blocker": "尚未回读对话", "next": "手动回读，或在看板主动启用自动分析以跟进后续完成的轮次。", "evidence": ["按稳定来源 ID 登记"], "cautions": ["自动分析会把聊天文字发给现有 Codex 配置的模型服务。"]}
            connection.execute("INSERT INTO custom_tasks VALUES (?,?)", (task["id"], json.dumps(task, ensure_ascii=False)))
    configuration = {"mode": "allowlist", "source_ids": sorted(allowed_sources() | set(identities))}
    with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, prefix=".tracking-", delete=False) as stream:
        json.dump(configuration, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.replace(ROOT / "tracking.json")


if __name__ == "__main__":
    main()
