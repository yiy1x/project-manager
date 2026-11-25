const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const crypto = require("node:crypto");

class Element {
  constructor(tag, document) {
    this.tagName = tag;
    this.document = document;
    this.children = [];
    this.dataset = {};
    this.value = "";
    this.className = "";
    this.open = false;
    this.disabled = false;
    this.classList = { toggle: (name, enabled) => {
      const classes = new Set(this.className.split(" ").filter(Boolean));
      if (enabled) classes.add(name);
      else classes.delete(name);
      this.className = [...classes].join(" ");
    } };
  }
  set textContent(text) { this.text = String(text); this.children = []; }
  get textContent() { return (this.text || "") + this.children.map(child => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  insertBefore(child, reference) {
    this.children = this.children.filter(node => node !== child);
    const index = reference ? this.children.indexOf(reference) : this.children.length;
    this.children.splice(index, 0, child);
  }
  replaceChildren(...children) { this.text = ""; this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  querySelectorAll(selector) {
    const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
    return descendants.filter(child => selector === "*" || (selector.startsWith(".") ? child.className.split(" ").includes(selector.slice(1)) : selector === "details[open]" ? child.tagName === "details" && child.open : child.tagName === selector));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { this.document.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
}

function snapshot(petQueue = "跟进中") {
  return {
    analysis_date: "2026-11-11", scanned_at: "2026-11-11T19:00:00+08:00", highlights: ["测试研判"], scope: "隔离测试", errors: [], token: "test-token",
    statistics: { chat_entries: 3, codex_threads: 3, claude_files: 0, import_duplicates: 0, gateway_files: 0, route_requests: 0, route_dates: [] }, entries: [],
    tasks: [["http400", "开发环境", "跟进中"], ["pet", "桌面体验", petQueue], ["manager", "项目总管", "跟进中"]].map(([id, project, queue]) => ({ id, project, queue, name: id, priority: "P1", stage: "分析基线", confidence: "测试", notes: [], sources: [], source_ids: [], evidence: [], cautions: [] })),
  };
}

function harness() {
  const nodes = new Map();
  const requests = [];
  const timers = new Map();
  const intervals = [];
  const streams = [];
  const listeners = {};
  let nextTimer = 0;
  const document = {
    activeElement: null,
    hidden: false,
    addEventListener: (name, callback) => { listeners[name] = callback; },
    createElement: tag => new Element(tag, document),
    getElementById: identity => nodes.get(identity),
    querySelectorAll: selector => [...nodes.values()].flatMap(node => node.querySelectorAll(selector)),
  };
  for (const identity of ["search", "notice", "date", "highlight", "sync", "scope", "count-scope", "refresh", "queues", "projects", "workspace", "inventory", "coverage", "inventory-rows", "task-list", "detail", "inventory-button", "claude-button", "page-title", "activity-sync", "claude-catalog", "claude-summary", "claude-results", "claude-live", "auto-review-toggle"]) {
    nodes.set(identity, document.createElement(identity === "search" ? "input" : "div"));
  }
  for (const queue of ["跟进中", "已完成", "暂不跟进"]) {
    const button = document.createElement("button");
    button.dataset.queue = queue;
    nodes.get("queues").append(button);
  }
  const context = vm.createContext({
    document, AbortController, crypto,
    EventSource: class {
      constructor(path) { this.path = path; this.listeners = {}; this.closed = false; streams.push(this); }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      close() { this.closed = true; }
      emit(data) { this.listeners.activity({ data: JSON.stringify(data) }); }
    },
    addEventListener: (name, callback) => { listeners[name] = callback; },
    fetch: (path, options = {}) => new Promise((resolveRequest, reject) => requests.push({ path, options, reject, respond: (data, status = 200) => resolveRequest({ ok: status >= 200 && status < 300, status, json: async () => data }) })),
    setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout: identity => timers.delete(identity),
    setInterval: callback => intervals.push(callback),
    confirm: () => true,
  });
  context.window = context;
  for (const filename of ["view-state.js", "ui.js"]) vm.runInContext(readFileSync(resolve(__dirname, "..", filename), "utf8"), context, { filename });
  const node = identity => nodes.get(identity);
  const sidebarCount = project => {
    const button = node("projects").children.find(button => button.textContent.startsWith(project));
    return button ? Number(button.querySelector("small").textContent) : null;
  };
  const queueButton = queue => node("queues").children.find(button => button.dataset.queue === queue);
  const selectPet = () => node("task-list").children.find(button => button.querySelector("h3")?.textContent === "pet").onclick();
  return { context, node, requests, intervals, timers, streams, listeners, sidebarCount, queueButton, selectPet };
}

const settle = () => new Promise(resolveTick => setImmediate(resolveTick));
async function ready() {
  const page = harness();
  page.requests[0].respond(snapshot());
  await settle();
  page.streams[0].emit(liveResult());
  return page;
}

function liveResult(extra = {}) {
  return { checked_at: new Date().toISOString(), statuses: {}, errors: [], gateway: { connected: true }, live_requests: [], ...extra };
}

test("恢复跟进从已完成或暂停切回目标任务，计数筛选与草稿保持一致", async () => {
  for (const previousQueue of ["已完成", "暂不跟进"]) {
    const page = harness();
    page.requests[0].respond(snapshot(previousQueue));
    await settle();
    page.queueButton(previousQueue).onclick();
    page.node("projects").children.find(button => button.textContent.startsWith("桌面体验")).onclick();
    const input = page.node("detail").querySelector("textarea");
    input.value = "恢复时保留草稿";
    input.oninput();
    const restoring = page.node("detail").querySelectorAll("button").find(button => button.textContent === "恢复跟进").onclick();
    page.requests[1].respond({ ok: true });
    await settle();
    page.requests[2].respond(snapshot());
    await restoring;
    await settle();
    assert.equal(page.queueButton("跟进中").className, "selected");
    assert.equal(page.queueButton("跟进中").textContent, "跟进中 1");
    assert.equal(page.sidebarCount("桌面体验"), 1);
    assert.equal(page.node("detail").querySelector("h2").textContent, "pet");
    assert.match(page.node("detail").textContent, /当前队列：跟进中/);
    assert.equal(page.node("detail").querySelector("textarea").value, "恢复时保留草稿");
    page.node("projects").children[0].onclick();
    assert.equal(page.queueButton("跟进中").textContent, "跟进中 3");
    assert.equal(page.queueButton(previousQueue).textContent, previousQueue + " 0");
  }
});

test("恢复成功但刷新失败时不抢切，后续核实成功才定位且不重复提交", async () => {
  const page = harness();
  page.requests[0].respond(snapshot("暂不跟进"));
  await settle();
  page.queueButton("暂不跟进").onclick();
  page.node("search").value = "暂不跟进";
  page.node("search").oninput();
  const saving = page.context.mutate("/api/queue", { task_id: "pet", queue: "跟进中" });
  page.requests[1].respond({ ok: true });
  await settle();
  page.requests[2].reject(new Error("refresh offline"));
  await saving;
  assert.equal(page.queueButton("暂不跟进").className, "selected");
  assert.match(page.node("notice").textContent, /已保存，但界面刷新失败/);
  const loading = page.context.load();
  page.requests[3].respond(snapshot());
  await loading;
  assert.equal(page.node("search").value, "");
  assert.match(page.node("detail").textContent, /当前队列：跟进中/);
  assert.equal(page.requests.filter(request => request.options.method === "POST").length, 1);
});

test("恢复跟进被拒绝时保留旧界面，等待期间用户切换不被强行拉回", async () => {
  const page = harness();
  page.requests[0].respond(snapshot("已完成"));
  await settle();
  page.queueButton("已完成").onclick();
  const denied = page.context.mutate("/api/queue", { task_id: "pet", queue: "跟进中" });
  page.requests[1].respond({ error: "failed" }, 400);
  await denied;
  assert.match(page.node("detail").textContent, /当前队列：已完成/);
  const restoring = page.context.mutate("/api/queue", { task_id: "pet", queue: "跟进中" });
  page.queueButton("暂不跟进").onclick();
  page.requests[2].respond({ ok: true });
  await settle();
  page.requests[3].respond(snapshot());
  await restoring;
  assert.equal(page.queueButton("暂不跟进").className, "selected");
});

test("状态驱动侧栏与任务重排，但不抢选中详情、提醒焦点或展开状态", async () => {
  const page = await ready();
  vm.runInContext('data.tasks.forEach(task => { task.source_ids = [task.id]; task.sources = [{id:task.id,updated:"2026-01-01T00:00:00Z"}]; }); render();', page.context);
  const input = page.node("detail").querySelector("textarea");
  input.value = "正在输入";
  input.oninput();
  input.focus();
  input.setSelectionRange(1, 2);
  const detail = page.node("detail").querySelector("details");
  detail.open = true;
  const originalTasks = [...page.node("task-list").children];
  page.streams[0].emit(liveResult({ statuses: { pet: { kind: "running", started_at: new Date().toISOString(), label: "进行中" } } }));
  assert.equal(page.node("task-list").children[0], originalTasks[1]);
  assert.equal(page.node("projects").children[1].dataset.orderKey, "桌面体验");
  assert.equal(page.node("detail").querySelector("h2").textContent, "http400");
  assert.equal(page.node("detail").querySelector("textarea"), input);
  assert.equal(page.context.document.activeElement, input);
  assert.equal(detail.open, true);
  assert.equal(input.value, "正在输入");
  page.streams[0].onerror();
  assert.equal(page.node("task-list").children[0], originalTasks[0]);
  assert.equal(page.node("detail").querySelector("textarea"), input);
});

test("关联聊天与Claude分组按同一优先规则更新，不重新检索或重建展开项", async () => {
  const page = await ready();
  vm.runInContext('data.entries = [{id:"a",title:"A",updated:"2026-01-01T00:00:00Z"},{id:"b",title:"B",updated:"2026-01-02T00:00:00Z"}];', page.context);
  page.node("inventory-button").onclick();
  assert.equal(page.node("inventory-rows").children[0].dataset.orderKey, "b");
  page.streams[0].emit(liveResult({ statuses: { a: { kind: "running" } } }));
  assert.equal(page.node("inventory-rows").children[0].dataset.orderKey, "a");
  page.node("claude-button").onclick();
  const group = catalogResult().projects[0];
  page.requests[1].respond({ ...catalogResult(), projects: [{ ...group, id: "recent", sessions: [{ ...group.sessions[0], id: "b", updated: "2026-01-02T00:00:00Z" }] }, { ...group, id: "older", sessions: [{ ...group.sessions[0], id: "a", updated: "2026-01-01T00:00:00Z" }] }] });
  await settle();
  const expanded = page.node("claude-results").querySelector("details");
  expanded.open = true;
  page.node("search").value = "keep";
  page.streams[0].emit(liveResult({ statuses: { b: { kind: "running" } } }));
  assert.equal(page.node("claude-results").children[0].dataset.orderKey, "recent");
  assert.ok(page.node("claude-results").querySelectorAll("details").includes(expanded));
  assert.equal(expanded.open, true);
  assert.equal(page.node("search").value, "keep");
  assert.equal(page.requests.length, 2);
});

test("自动分析显示已读位置，失败可重试，版本事件触发刷新", async () => {
  const page = await ready();
  const updated = snapshot();
  updated.auto_review = { enabled: true, revision: 1 };
  updated.tasks[1].needs_review = true;
  updated.tasks[1].auto_analysis = { state: "failed", label: "自动分析失败", reviewed_through: "2026-11-13T16:51:00+08:00", error: "回读超时" };
  const loading = page.context.load();
  page.requests[1].respond(updated);
  await loading;
  page.selectPet();
  assert.match(page.node("detail").querySelector(".analysis-status").textContent, /已分析至.*自动分析失败/);
  assert.equal(page.node("detail").querySelector(".analysis-status").title, "回读超时");
  const retry = page.node("detail").querySelectorAll("button").find(button => button.textContent === "重试分析");
  assert.ok(retry);
  page.streams[0].emit(liveResult({ analysis_revision: 2 }));
  assert.equal(page.requests[2].path, "/api/state");
  page.requests[2].respond({ ...updated, auto_review: { enabled: true, revision: 2 } });
  await settle();
  page.streams[0].emit(liveResult({ analysis_revision: 2 }));
  assert.equal(page.requests.length, 3);
});

test("前沿总纲只计一项任务，三个预留方向不显示为聊天或运行状态", async () => {
  const page = await ready();
  const updated = snapshot();
  const task = { ...updated.tasks[0], id: "frontier-research", project: "前沿新闻", name: "前沿调研基础模块（总纲）", planned_branches: [{ name: "科研", focus: "任务、方法与评测。" }, { name: "工业", focus: "交付、成本与算力。" }, { name: "社会", focus: "金融、就业与政策。" }] };
  updated.tasks.push(task);
  const refreshing = page.context.load();
  page.requests[1].respond(updated);
  await refreshing;
  assert.equal(page.sidebarCount("前沿新闻"), 1);
  assert.equal(page.sidebarCount("全部项目"), 4);
  page.node("projects").children.find(button => button.textContent.startsWith("前沿新闻")).onclick();
  const plan = page.node("detail").querySelector(".branch-plan");
  assert.match(plan.textContent, /等待你创建分支聊天；尚未关联，不计入任务数/);
  assert.deepEqual(plan.querySelectorAll("strong").map(node => node.textContent), ["科研", "工业", "社会"]);
  assert.equal(plan.querySelectorAll("li").length, 3);
  for (const selector of ["a", "button", ".activity"]) assert.equal(plan.querySelectorAll(selector).length, 0);
  assert.equal(page.node("task-list").querySelectorAll("h3").length, 1);
  assert.equal(page.queueButton("跟进中").textContent, "跟进中 1");
  const anotherRefresh = page.context.load();
  page.requests[2].respond(updated);
  await anotherRefresh;
  assert.equal(page.sidebarCount("前沿新闻"), 1);
  assert.equal(page.node("detail").querySelectorAll(".branch-plan").length, 1);
  page.node("projects").children.find(button => button.textContent.startsWith("项目总管")).onclick();
  assert.equal(page.node("detail").querySelector(".branch-plan"), null);
});

test("初次读取前搜索与导航不报错，读取失败可手动重试", async () => {
  const page = harness();
  page.node("search").oninput();
  page.queueButton("已完成").onclick();
  page.node("inventory-button").onclick();
  page.requests[0].reject(new Error("offline"));
  await settle();
  const retry = page.node("refresh").onclick();
  page.requests[1].respond(snapshot());
  await retry;
  assert.equal(page.sidebarCount("全部项目"), 0);
});

test("结项后旧读取不得覆盖新状态，重复写入被禁用，详情同步切换", async () => {
  const page = await ready();
  page.selectPet();
  const olderRead = page.context.load();
  const saving = page.context.mutate("/api/queue", { task_id: "pet", queue: "已完成" });
  assert.equal(page.node("refresh").disabled, true);
  assert.equal(await page.context.mutate("/api/queue", { task_id: "pet", queue: "已完成" }), false);
  assert.equal(page.requests[2].options.headers["X-Manager-Token"], "test-token");
  page.requests[2].respond({ ok: true });
  await settle();
  page.requests[3].respond(snapshot("已完成"));
  await saving;
  page.requests[1].respond(snapshot());
  await olderRead;
  assert.equal(page.sidebarCount("桌面体验"), null);
  assert.equal(page.sidebarCount("全部项目"), 2);
  assert.equal(page.queueButton("已完成").textContent, "已完成 1");
  assert.equal(page.node("detail").querySelector("h2").textContent, "http400");
  assert.equal(page.node("refresh").disabled, false);
  page.queueButton("已完成").onclick();
  assert.equal(page.sidebarCount("桌面体验"), 1);
  assert.equal(page.node("detail").querySelector("h2").textContent, "pet");
  assert.match(page.node("detail").textContent, /当前队列：已完成/);
});

test("轮询改变当前任务时不会保留旧详情", async () => {
  const page = await ready();
  page.selectPet();
  page.intervals[0]();
  page.requests[1].respond(snapshot("已完成"));
  await settle();
  assert.equal(page.node("detail").querySelector("h2").textContent, "http400");
  assert.equal(page.sidebarCount("桌面体验"), null);
});

test("提醒草稿、焦点、选区、展开状态在刷新与切换任务后保留", async () => {
  const page = await ready();
  page.selectPet();
  const textarea = page.node("detail").querySelector("textarea");
  textarea.value = "未保存草稿";
  textarea.oninput();
  textarea.focus();
  textarea.setSelectionRange(1, 3);
  page.node("detail").querySelector("details").open = true;
  page.intervals[0]();
  page.requests[1].respond(snapshot());
  await settle();
  const refreshed = page.node("detail").querySelector("textarea");
  assert.equal(refreshed.value, "未保存草稿");
  assert.equal(page.context.document.activeElement, refreshed);
  assert.deepEqual([refreshed.selectionStart, refreshed.selectionEnd], [1, 3]);
  assert.equal(page.node("detail").querySelector("details").open, true);
  page.node("task-list").children[0].onclick();
  assert.equal(page.node("detail").querySelector("textarea").value, "");
  page.selectPet();
  assert.equal(page.node("detail").querySelector("textarea").value, "未保存草稿");
});

test("提醒保存期间继续输入的新草稿不会被清除", async () => {
  const page = await ready();
  page.selectPet();
  const first = page.node("detail").querySelector("textarea");
  first.value = "第一条";
  first.oninput();
  const saving = page.context.mutate("/api/notes", { task_id: "pet", text: "第一条" });
  const next = page.node("detail").querySelector("textarea");
  next.value = "下一条";
  next.oninput();
  page.requests[1].respond({ ok: true });
  await settle();
  const updated = snapshot();
  updated.tasks[1].notes = [{ id: 1, text: "第一条", pinned: 0, done: 0 }];
  page.requests[2].respond(updated);
  await saving;
  assert.equal(page.node("detail").querySelector("textarea").value, "下一条");
  assert.match(page.node("detail").textContent, /第一条/);
});

function timelineFixture() {
  const result = snapshot();
  result.tasks[0].timeline = Array.from({ length: 7 }, (_, index) => ({ id: "event-" + index, kind: "实现", focus: index === 6 ? "验证入口" : "接入代码", title: `阶段 ${index}`, detail: "保留阶段变化而非提交明细", occurred_at: "2026-11-11T18:00:00+08:00", recorded_at: "2026-11-12", precision: "second", origin: "review", evidence: [{ source_id: "codex:source", ordinal: index, text: "依据文字" }] }));
  return result;
}

const timelineInput = (page, field) => page.node("detail").querySelectorAll("*").find(node => node.dataset.field === "timeline-" + field);

test("时间线默认最近四项，旧节点可展开，按关注点筛选并保留状态", async () => {
  const page = harness();
  page.requests[0].respond(timelineFixture());
  await settle();
  assert.equal(page.node("detail").querySelector(".timeline-list").children.length, 4);
  assert.equal(page.node("detail").querySelector(".timeline-history").open, false);
  assert.match(page.node("detail").textContent, /展开较早的 3 个节点/);
  const filter = page.node("detail").querySelector(".timeline-filter");
  filter.value = "验证入口";
  filter.onchange();
  assert.equal(page.node("detail").querySelectorAll(".timeline-node").length, 1);
  assert.match(page.node("detail").querySelector(".timeline-node").textContent, /阶段 6/);
  page.node("detail").querySelector(".timeline-evidence").open = true;
  const loading = page.context.load();
  page.requests[1].respond(timelineFixture());
  await loading;
  assert.equal(page.node("detail").querySelector(".timeline-filter").value, "验证入口");
  assert.equal(page.node("detail").querySelector(".timeline-evidence").open, true);
  page.selectPet();
  page.node("task-list").children[0].onclick();
  assert.equal(page.node("detail").querySelector(".timeline-filter").value, "验证入口");
  assert.equal(page.node("detail").querySelector(".timeline-evidence").open, true);
});

test("时间线和提醒草稿独立，多字段焦点、选区和展开状态不被刷新吞掉", async () => {
  const page = await ready();
  page.node("detail").querySelector(".timeline-add").open = true;
  const note = page.node("detail").querySelector("textarea");
  note.value = "下次继续";
  note.oninput();
  const title = timelineInput(page, "title");
  title.value = "第一部分代码";
  title.oninput();
  const detail = timelineInput(page, "detail");
  detail.value = "新的关注点";
  detail.oninput();
  detail.focus();
  detail.setSelectionRange(1, 4);
  const loading = page.context.load();
  page.requests[1].respond(snapshot());
  await loading;
  assert.equal(timelineInput(page, "title").value, "第一部分代码");
  assert.equal(timelineInput(page, "detail").value, "新的关注点");
  assert.equal(page.context.document.activeElement, timelineInput(page, "detail"));
  assert.equal(timelineInput(page, "detail").selectionStart, 1);
  assert.equal(page.node("detail").querySelector("textarea").value, "下次继续");
  assert.equal(page.node("detail").querySelector(".timeline-add").open, true);
  page.selectPet();
  assert.equal(timelineInput(page, "title").value, "");
  page.node("task-list").children[0].onclick();
  assert.equal(timelineInput(page, "title").value, "第一部分代码");
  assert.equal(page.node("detail").querySelector(".timeline-add").open, true);
});

test("保存阶段节点使用稳定幂等标识，失败可重试，继续输入不丢失", async () => {
  const page = await ready();
  const title = timelineInput(page, "title");
  title.value = "第一阶段";
  title.oninput();
  page.node("detail").querySelector(".timeline-form").onsubmit({ preventDefault() {} });
  const first = JSON.parse(page.requests[1].options.body);
  page.requests[1].reject(new Error("response lost"));
  await settle();
  assert.equal(timelineInput(page, "title").value, "第一阶段");
  page.node("detail").querySelector(".timeline-form").onsubmit({ preventDefault() {} });
  const retry = JSON.parse(page.requests[2].options.body);
  assert.equal(first.id, retry.id);
  const newer = timelineInput(page, "title");
  newer.value = "第二阶段";
  newer.oninput();
  page.requests[2].respond({ ok: true });
  await settle();
  page.requests[3].respond(snapshot());
  await settle();
  assert.equal(timelineInput(page, "title").value, "第二阶段");
  assert.equal(page.sidebarCount("全部项目"), 3);
  assert.match(page.node("notice").textContent, /当前队列与进度摘要保持不变/);
});

test("节点保存成功清空相同草稿，不清空提醒，不误报新消息已分析", async () => {
  const page = await ready();
  const title = timelineInput(page, "title");
  title.value = "保存记录";
  title.oninput();
  const note = page.node("detail").querySelector("textarea");
  note.value = "下一步提醒";
  note.oninput();
  page.node("detail").querySelector(".timeline-form").onsubmit({ preventDefault() {} });
  page.requests[1].respond({ ok: true });
  await settle();
  const updated = snapshot();
  updated.tasks[0].needs_review = true;
  page.requests[2].respond(updated);
  await settle();
  assert.equal(timelineInput(page, "title").value, "");
  assert.equal(page.node("detail").querySelector("textarea").value, "下一步提醒");
  assert.match(page.node("detail").textContent, /时间线尚未覆盖新增进展/);
});

test("时间线内容按纯文本呈现，不执行历史脚本或外部链接", async () => {
  const page = harness();
  const updated = timelineFixture();
  updated.tasks[0].timeline[0].title = "<script>alert(1)</script>";
  updated.tasks[0].sources = [{ id: "codex:source", title: "来源", link: "javascript:alert(1)" }];
  page.requests[0].respond(updated);
  await settle();
  assert.match(page.node("detail").textContent, /<script>alert\(1\)<\/script>/);
  assert.equal(page.node("detail").querySelectorAll("script").length, 0);
  assert.equal(page.node("detail").querySelector(".timeline").querySelectorAll("a").length, 0);
});

test("时间线先显示加粗短结论和分项短句，长原文只放折叠区", async () => {
  const page = harness();
  const updated = timelineFixture();
  updated.tasks[0].timeline[0] = { ...updated.tasks[0].timeline[0], summary: "程序通过，面板待验", points: [{ label: "结果", text: "原助手报告 12 组通过。" }, { label: "待验", text: "真实面板。" }] };
  page.requests[0].respond(updated);
  await settle();
  const node = page.node("detail").querySelector(".timeline-node");
  assert.equal(node.children[0].tagName, "h4");
  assert.equal(node.children[0].querySelector("strong").textContent, "程序通过，面板待验");
  assert.equal(node.querySelector(".timeline-points").children.length, 2);
  assert.equal(node.querySelector(".timeline-outcome"), null);
  assert.match(node.querySelector(".timeline-points").textContent, /原助手报告 12 组通过。待验：真实面板。/);
  const evidence = node.querySelector(".timeline-evidence");
  assert.equal(evidence.open, false);
  assert.match(evidence.textContent, /保留阶段变化而非提交明细/);
  assert.match(evidence.textContent, /消息序号 0/);
});

test("结构化要点仍按纯文本渲染，旧段落不截断", async () => {
  const page = harness();
  const updated = timelineFixture();
  updated.tasks[0].timeline[0].points = [{ label: "<b>", text: "<script>alert(1)</script>" }];
  updated.tasks[0].timeline[1].detail = "长".repeat(200) + "但面板尚未验收";
  page.requests[0].respond(updated);
  await settle();
  const nodes = page.node("detail").querySelectorAll(".timeline-node");
  assert.match(nodes[0].querySelector(".timeline-points").textContent, /<script>alert\(1\)<\/script>/);
  assert.equal(nodes[0].querySelectorAll("script").length, 0);
  assert.equal(nodes[0].querySelectorAll("b").length, 0);
  assert.equal(nodes[1].querySelector(".timeline-outcome").textContent, updated.tasks[0].timeline[1].detail);
});

test("成功保存清空已提交草稿，失败保存保留草稿", async () => {
  for (const successful of [true, false]) {
    const page = await ready();
    const textarea = page.node("detail").querySelector("textarea");
    textarea.value = "提醒";
    textarea.oninput();
    const saving = page.context.mutate("/api/notes", { task_id: "http400", text: "提醒" });
    page.requests[1].respond(successful ? { ok: true } : { error: "数据库失败" }, successful ? 200 : 400);
    await settle();
    if (successful) page.requests[2].respond(snapshot());
    await saving;
    assert.equal(page.node("detail").querySelector("textarea").value, successful ? "" : "提醒");
    assert.match(page.node("notice").textContent, successful ? /已保存/ : /未保存/);
  }
});

test("保存成功但刷新失败时明确告知，不能显示计数已更新", async () => {
  const page = await ready();
  const saving = page.context.mutate("/api/queue", { task_id: "pet", queue: "已完成" });
  page.requests[1].respond({ ok: true });
  await settle();
  page.requests[2].reject(new Error("offline"));
  await saving;
  assert.match(page.node("notice").textContent, /已保存，但界面刷新失败/);
  assert.doesNotMatch(page.node("notice").textContent, /计数已更新/);
  assert.equal(page.node("refresh").disabled, false);
  assert.equal(page.timers.size, 1);
});

test("令牌过期仅重新读取状态，不自动重放写操作", async () => {
  const page = await ready();
  const saving = page.context.mutate("/api/queue", { task_id: "pet", queue: "已完成" });
  page.requests[1].respond({ error: "请求来源校验失败" }, 403);
  await settle();
  page.requests[2].respond({ ...snapshot(), token: "new-token" });
  await saving;
  assert.equal(page.requests.filter(request => request.options.method === "POST").length, 1);
  assert.match(page.node("notice").textContent, /未保存/);
  assert.equal(page.sidebarCount("桌面体验"), 1);
});

test("写入连接中断时不误报未保存，刷新后以数据库为准", async () => {
  const page = await ready();
  const saving = page.context.mutate("/api/queue", { task_id: "pet", queue: "已完成" });
  page.requests[1].reject(new Error("connection lost"));
  await saving;
  assert.match(page.node("notice").textContent, /保存结果未确认/);
  assert.doesNotMatch(page.node("notice").textContent, /未保存：/);
  const reloading = page.context.load();
  page.requests[2].respond(snapshot("已完成"));
  await reloading;
  assert.equal(page.sidebarCount("桌面体验"), null);
});

test("跟进范围变化移除当前项目时自动返回全部项目", async () => {
  const page = await ready();
  page.node("projects").children.find(button => button.textContent.startsWith("桌面体验")).onclick();
  const reloading = page.context.load();
  const updated = snapshot();
  updated.tasks = updated.tasks.filter(task => task.id !== "pet");
  page.requests[1].respond(updated);
  await reloading;
  assert.equal(page.sidebarCount("全部项目"), 2);
  assert.equal(page.node("detail").querySelector("h2").textContent, "http400");
  assert.equal(page.node("projects").children[0].className, "selected");
});

test("简约视图保留动作与提醒，完整分析默认折叠", async () => {
  const page = await ready();
  assert.equal(page.node("page-title").textContent, "我的任务");
  assert.equal(page.node("detail").querySelector(".analysis-detail").open, false);
  assert.match(page.node("detail").querySelector(".analysis-detail").textContent, /完整分析与依据/);
  assert.match(page.node("detail").textContent, /我确认结项/);
  assert.ok(page.node("detail").querySelector("textarea"));
});

test("状态刷新不重建表单、不改变用户队列，断线不保留进行中", async () => {
  const page = await ready();
  const textarea = page.node("detail").querySelector("textarea");
  vm.runInContext('data.tasks[0].source_ids = ["codex:one"]; render();', page.context);
  const currentInput = page.node("detail").querySelector("textarea");
  page.streams[0].emit(liveResult({ statuses: { "codex:one": { kind: "running", label: "进行中", basis: "本地记录", updated_at: new Date().toISOString() } } }));
  assert.equal(page.node("detail").querySelector("textarea"), currentInput);
  assert.notEqual(currentInput, textarea);
  assert.match(page.node("detail").textContent, /进行中/);
  assert.equal(page.sidebarCount("全部项目"), 3);
  page.streams[0].onerror();
  assert.equal(page.node("detail").querySelector(".activity").textContent, "状态不可用");
  assert.match(page.node("activity-sync").textContent, /同步中断/);
});

test("Claude检索隔离于跟进任务，旧结果不得覆盖新关键词", async () => {
  const page = await ready();
  page.node("claude-button").onclick();
  assert.equal(page.node("workspace").hidden, true);
  assert.equal(page.node("claude-catalog").hidden, false);
  assert.equal(page.node("page-title").textContent, "Claude Code");
  page.node("search").value = "new";
  const latest = page.context.loadCatalog();
  const result = { project_count: 1, session_count: 1, matched_count: 1, available: true, errors: [], projects: [{ name: "project-new", path: "/work/new", session_count: 1, sessions: [{ id: "claude:test", title: "latest-title", preview: "matched preview", updated: new Date().toISOString(), resume_command: "claude --resume test" }] }] };
  page.requests[2].respond(result);
  await latest;
  page.requests[1].respond({ ...result, projects: [] });
  await settle();
  assert.match(page.node("claude-results").textContent, /latest-title/);
  assert.match(page.node("claude-results").textContent, /继续此聊天/);
  assert.equal(page.sidebarCount("全部项目"), 3);
  page.node("projects").children[0].onclick();
  assert.equal(page.node("workspace").hidden, false);
  assert.equal(page.node("search").value, "");
  assert.equal(page.node("detail").querySelector("h2").textContent, "http400");
});

test("Claude检索失败时显示重试入口，不冒充没有结果", async () => {
  const page = await ready();
  page.node("claude-button").onclick();
  page.requests[1].respond({ error: "读取失败" }, 500);
  await settle();
  assert.match(page.node("claude-summary").textContent, /检索失败，请点刷新重试/);
  assert.equal(page.node("claude-results").children.length, 0);
});

test("任务数据读取失败也可以独立打开Claude检索", async () => {
  const page = harness();
  page.requests[0].reject(new Error("state offline"));
  await settle();
  page.node("claude-button").onclick();
  assert.equal(page.node("claude-catalog").hidden, false);
  assert.equal(page.node("workspace").hidden, true);
  page.requests[1].respond({ project_count: 0, session_count: 0, matched_count: 0, available: true, errors: [], projects: [] });
  await settle();
  assert.match(page.node("claude-results").textContent, /没有匹配的聊天/);
});

test("过期的状态响应不能继续显示为进行中", async () => {
  const page = await ready();
  vm.runInContext('data.tasks[0].source_ids = ["codex:one"]; render();', page.context);
  page.streams[0].emit(liveResult({ checked_at: new Date(Date.now() - 26000).toISOString(), statuses: { "codex:one": { kind: "running", label: "进行中" } } }));
  assert.equal(page.node("detail").querySelector(".activity").textContent, "状态不可用");
  assert.match(page.node("activity-sync").textContent, /同步中断/);
});

function catalogResult(title = "Claude测试") {
  return { project_count: 1, session_count: 1, matched_count: 1, available: true, errors: [], projects: [{ name: "project", path: "/work/project", session_count: 1, sessions: [{ id: "claude:test", title, preview: "内容", updated: "2026-11-11T19:00:00+08:00", resume_command: "claude --resume test" }] }] };
}

test("Claude目录按需刷新只更新变化内容，保留搜索与展开状态", async () => {
  const page = await ready();
  page.node("claude-button").onclick();
  page.requests[1].respond(catalogResult());
  await settle();
  const original = page.node("claude-results").children[0];
  page.node("claude-results").querySelector("details").open = true;
  const unchanged = page.context.loadCatalog();
  page.requests[2].respond(catalogResult());
  await unchanged;
  assert.equal(page.node("claude-results").children[0], original);
  const changed = page.context.loadCatalog();
  page.requests[3].respond(catalogResult("新对话标题"));
  await changed;
  assert.match(page.node("claude-results").textContent, /新对话标题/);
  assert.equal(page.node("claude-results").querySelector("details").open, true);
  assert.equal(page.sidebarCount("全部项目"), 3);
});

test("状态心跳不触发检索，隐藏页面停止订阅和任务轮询", async () => {
  const page = await ready();
  page.node("claude-button").onclick();
  page.intervals[1]();
  assert.equal(page.requests.length, 2);
  page.requests[1].respond(catalogResult());
  await settle();
  page.context.document.hidden = true;
  page.listeners.visibilitychange();
  page.intervals.forEach(callback => callback());
  assert.equal(page.requests.length, 2);
  assert.equal(page.streams[0].closed, true);
  page.context.document.hidden = false;
  page.listeners.visibilitychange();
  assert.equal(page.streams.length, 2);
  assert.equal(page.requests.length, 2);
});

test("猫老大实时请求显示到未落盘Claude条目，断线立即清掉旧请求", async () => {
  const page = await ready();
  page.streams[0].emit(liveResult({ live_requests: [{ source_id: "claude:unsaved", folder: "new/project", model: "test-model", label: "模型请求中" }] }));
  assert.match(page.node("activity-sync").textContent, /猫老大已连接/);
  assert.match(page.node("claude-live").textContent, /new\/project/);
  assert.match(page.node("claude-live").textContent, /当前有 1 个模型请求/);
  page.streams[0].emit(liveResult({ gateway: { connected: false, error: "offline" } }));
  assert.match(page.node("activity-sync").textContent, /猫老大未连接/);
  assert.doesNotMatch(page.node("claude-live").textContent, /new\/project/);
  assert.equal(page.sidebarCount("全部项目"), 3);
});

test("刷新目录失败会标记旧结果，恢复后清除异常提示", async () => {
  const page = await ready();
  page.node("claude-button").onclick();
  page.requests[1].respond(catalogResult());
  await settle();
  const failed = page.context.loadCatalog();
  page.requests[2].reject(new Error("offline"));
  await failed;
  assert.match(page.node("claude-summary").textContent, /可能已过期/);
  assert.match(page.node("claude-results").textContent, /Claude测试/);
  const recovered = page.context.loadCatalog();
  page.requests[3].respond(catalogResult());
  await recovered;
  assert.doesNotMatch(page.node("claude-summary").textContent, /过期/);
});

test("Codex和Claude共用一条订阅，事件不重读目录且保留展开与关键词", async () => {
  const page = await ready();
  vm.runInContext('data.tasks[0].source_ids = ["codex:one"]; render();', page.context);
  page.node("claude-button").onclick();
  page.requests[1].respond(catalogResult());
  await settle();
  page.node("search").value = "关键词";
  const details = page.node("claude-results").querySelector("details");
  details.open = true;
  page.streams[0].emit(liveResult({ statuses: { "claude:test": { label: "模型请求中", kind: "running" }, "codex:one": { label: "模型请求中", kind: "running" } } }));
  assert.match(page.node("claude-results").textContent, /模型请求中/);
  assert.equal(page.node("detail").querySelector(".activity").textContent, "模型请求中");
  assert.equal(page.node("claude-results").querySelector("details"), details);
  assert.equal(details.open, true);
  assert.equal(page.node("search").value, "关键词");
  assert.equal(page.streams.length, 1);
  assert.equal(page.streams[0].path, "/api/activity/events");
  assert.equal(page.requests.length, 2);
  assert.equal(page.intervals.length, 2);
});

test("任务初次读取失败不阻止状态订阅，断线重连后忽略旧连接事件", async () => {
  const page = harness();
  page.requests[0].reject(new Error("offline"));
  await settle();
  const first = page.streams[0];
  first.emit(liveResult());
  assert.match(page.node("activity-sync").textContent, /实时订阅/);
  page.listeners.pagehide();
  page.listeners.pageshow();
  page.streams[1].emit(liveResult());
  first.onerror();
  first.emit(liveResult({ gateway: { connected: false } }));
  assert.match(page.node("activity-sync").textContent, /实时订阅/);
  assert.equal(page.requests.length, 1);
  page.streams[1].listeners.activity({ data: "invalid-json" });
  assert.match(page.node("activity-sync").textContent, /同步中断/);
});

test("无心跳时清空过期状态并重连，不重新请求历史", async () => {
  const page = await ready();
  vm.runInContext('activityReceivedAt = Date.now() - 26000;', page.context);
  page.intervals[1]();
  assert.equal(page.streams[0].closed, true);
  assert.equal(page.streams.length, 2);
  assert.equal(page.requests.length, 1);
  assert.match(page.node("activity-sync").textContent, /同步中断/);
});

test("当前项目最后一项结项后隐藏，切换队列只显示对应非零项目", async () => {
  const page = await ready();
  page.node("projects").children.find(button => button.textContent.startsWith("桌面体验")).onclick();
  const saving = page.context.mutate("/api/queue", { task_id: "pet", queue: "已完成" });
  page.requests[1].respond({ ok: true });
  await settle();
  page.requests[2].respond(snapshot("已完成"));
  await saving;
  assert.equal(page.sidebarCount("桌面体验"), null);
  assert.equal(page.node("projects").children[0].className, "selected");
  assert.equal(page.node("detail").querySelector("h2").textContent, "http400");
  page.queueButton("已完成").onclick();
  assert.equal(page.sidebarCount("桌面体验"), 1);
  assert.equal(page.sidebarCount("开发环境"), null);
  assert.equal(page.sidebarCount("项目总管"), null);
  assert.equal(page.node("projects").children.length, 2);
  page.node("projects").children[1].onclick();
  page.queueButton("跟进中").onclick();
  assert.equal(page.sidebarCount("全部项目"), 2);
  assert.equal(page.sidebarCount("桌面体验"), null);
  assert.equal(page.node("projects").children[0].className, "selected");
  page.queueButton("暂不跟进").onclick();
  assert.equal(page.node("projects").children.length, 1);
  assert.equal(page.sidebarCount("全部项目"), 0);
  assert.match(page.node("task-list").textContent, /暂无任务/);
});

test("新增项目随数据更新出现，搜索匹配为零时隐藏，清空后恢复", async () => {
  const page = await ready();
  const updated = snapshot("已完成");
  updated.tasks.push({ ...updated.tasks[0], id: "new-project", project: "新项目", name: "新任务" });
  page.intervals[0]();
  page.requests[1].respond(updated);
  await settle();
  assert.equal(page.sidebarCount("新项目"), 1);
  assert.equal(page.sidebarCount("桌面体验"), null);
  page.node("projects").children.find(button => button.textContent.startsWith("开发环境")).onclick();
  page.node("search").value = "新任务";
  page.node("search").oninput();
  assert.equal(page.sidebarCount("开发环境"), null);
  assert.equal(page.sidebarCount("新项目"), 1);
  assert.equal(page.node("projects").children[0].className, "selected");
  assert.equal(page.node("detail").querySelector("h2").textContent, "新任务");
  page.node("search").value = "";
  page.node("search").oninput();
  assert.equal(page.sidebarCount("开发环境"), 1);
  assert.equal(page.sidebarCount("全部项目"), 3);
});

test("最近自动回读时间独立于人工基线，检测故障可见且恢复后清除", async () => {
  const page = await ready();
  const updated = snapshot();
  updated.auto_review = { enabled: true, last_success_at: "2026-11-16T10:45:25+08:00", error: "测试任务：读取失败" };
  page.intervals[0]();
  page.requests[1].respond(updated);
  await settle();
  assert.match(page.node("sync").textContent, /最近自动回读.*2026\/11\/16/);
  assert.match(page.node("sync").textContent, /检测异常：测试任务：读取失败/);
  assert.doesNotMatch(page.node("sync").textContent, /分析基线/);
  updated.auto_review.error = "";
  page.intervals[0]();
  page.requests[2].respond(updated);
  await settle();
  assert.doesNotMatch(page.node("sync").textContent, /检测异常|读取失败/);
});

test("下一步先显示粗体总结和短行动，长原文只保留在完整分析", async () => {
  const page = await ready();
  const updated = snapshot();
  updated.tasks[0].next = "原始后续说明".repeat(50);
  updated.tasks[0].next_plan = { summary: "先验收桌面接入，再确认手机方案", points: [{ label: "验收", text: "检查登录、取消和异常恢复。" }, { label: "实测", text: "先授权，只用合成资料验证。" }] };
  updated.tasks[0].needs_review = true;
  page.intervals[0]();
  page.requests[1].respond(updated);
  await settle();
  const next = page.node("detail").querySelector(".next-step");
  assert.equal(next.querySelector(".next-summary").querySelector("strong").textContent, updated.tasks[0].next_plan.summary);
  assert.equal(next.querySelector("ul").children.length, 2);
  assert.match(next.textContent, /以下是上轮建议/);
  assert.doesNotMatch(next.textContent, /原始后续说明/);
  assert.match(page.node("detail").querySelector(".analysis-detail").textContent, /原始后续说明/);
  assert.equal(page.node("detail").querySelector(".analysis-detail").open, false);
});

test("待确认显示粗体总结和短要点，保留完整风险并标注上轮分析", async () => {
  const page = await ready();
  const updated = snapshot();
  updated.tasks[0].blocker = "不能遗漏的原始限制".repeat(60);
  updated.tasks[0].confirmation_plan = { summary: "真实调用与手机功能仍待验收", points: [{ label: "验证", text: "模拟通过不等于真实调用通过。" }, { label: "手机", text: "未实现的登录能力不算完成。" }] };
  updated.tasks[0].needs_review = true;
  page.intervals[0]();
  page.requests[1].respond(updated);
  await settle();
  const brief = page.node("detail").querySelector(".confirmation");
  assert.equal(brief.querySelector("strong").textContent, updated.tasks[0].confirmation_plan.summary);
  assert.equal(brief.querySelector("ul").children.length, 2);
  assert.match(brief.textContent, /基于上轮分析/);
  assert.doesNotMatch(brief.textContent, /不能遗漏的原始限制/);
  assert.match(page.node("detail").querySelector(".analysis-detail").textContent, /不能遗漏的原始限制/);
  assert.equal(page.node("detail").querySelector(".analysis-detail").open, false);
});

test("待确认兼容旧文和无待确认项，不执行文字脚本，结项后隐藏", async () => {
  const page = await ready();
  const updated = snapshot();
  updated.tasks[0].blocker = "完整旧说明".repeat(80);
  page.intervals[0]();
  page.requests[1].respond(updated);
  await settle();
  const original = page.node("detail").querySelector(".confirmation").querySelector("details");
  assert.equal(original.open, false);
  assert.equal(original.querySelector("p").textContent, updated.tasks[0].blocker);
  updated.tasks[0].confirmation_plan = { summary: "暂无明确待确认事项", points: [] };
  page.intervals[0]();
  page.requests[2].respond(updated);
  await settle();
  assert.equal(page.node("detail").querySelector(".confirmation").querySelector("ul"), null);
  updated.tasks[0].confirmation_plan = { summary: "<script>bad()</script>", points: [{ label: "验证", text: "<img src=x onerror=bad()>" }] };
  page.intervals[0]();
  page.requests[3].respond(updated);
  await settle();
  const brief = page.node("detail").querySelector(".confirmation");
  assert.match(brief.textContent, /<script>/);
  assert.equal(brief.querySelectorAll("script").length, 0);
  assert.equal(brief.querySelectorAll("img").length, 0);
  updated.tasks[0].queue = "已完成";
  page.intervals[0]();
  page.requests[4].respond(updated);
  await settle();
  page.queueButton("已完成").onclick();
  assert.equal(page.node("detail").querySelector(".confirmation"), null);
});

test("旧下一步完整折叠不截断，结构化内容只按文字显示", async () => {
  const page = await ready();
  const updated = snapshot();
  updated.tasks[0].next = "保留所有限制".repeat(70);
  page.intervals[0]();
  page.requests[1].respond(updated);
  await settle();
  const original = page.node("detail").querySelector(".next-step").querySelector("details");
  assert.equal(original.open, false);
  assert.equal(original.querySelector("p").textContent, updated.tasks[0].next);
  updated.tasks[0].next_plan = { summary: "<script>bad()</script>", points: [{ label: "<b>", text: "<img src=x onerror=bad()>" }] };
  page.intervals[0]();
  page.requests[2].respond(updated);
  await settle();
  const next = page.node("detail").querySelector(".next-step");
  assert.match(next.textContent, /<script>/);
  assert.equal(next.querySelectorAll("script").length, 0);
  assert.equal(next.querySelectorAll("img").length, 0);
  updated.tasks[0].queue = "已完成";
  page.intervals[0]();
  page.requests[3].respond(updated);
  await settle();
  page.queueButton("已完成").onclick();
  assert.match(page.node("detail").querySelector(".next-step").textContent, /此任务已由你确认完成/);
  assert.equal(page.node("detail").querySelector(".next-step").querySelector("ul"), null);
});

test("新一轮显示最新要求但不替换上轮成果，结束后移除临时预览", async () => {
  const page = await ready();
  const updated = snapshot();
  const task = updated.tasks[0];
  task.needs_review = true;
  task.sources = [{ id: task.source_ids[0], latest_request: "把最近动态移到研究与实践上方<script>bad()</script>", saved_turn_status: { status: "inProgress" } }];
  const originalStage = task.stage;
  const originalTimeline = JSON.stringify(task.timeline);
  page.intervals[0]();
  page.requests[1].respond(updated);
  await settle();
  assert.match(page.node("task-list").textContent, /最新要求：把最近动态移到研究与实践上方/);
  assert.match(page.node("detail").textContent, /最新要求（未交付）/);
  assert.match(page.node("detail").textContent, /上轮进度：/);
  assert.equal(page.node("detail").querySelectorAll("script").length, 0);
  assert.equal(task.stage, originalStage);
  assert.equal(JSON.stringify(task.timeline), originalTimeline);
  task.sources[0].saved_turn_status.status = "completed";
  page.intervals[0]();
  page.requests[2].respond(updated);
  await settle();
  assert.doesNotMatch(page.node("detail").textContent, /最新要求（未交付）/);
  task.sources[0].saved_turn_status.status = "inProgress";
  task.sources[0].source_stale = true;
  page.intervals[0]();
  page.requests[3].respond(updated);
  await settle();
  assert.doesNotMatch(page.node("detail").textContent, /最新要求（未交付）/);
});
