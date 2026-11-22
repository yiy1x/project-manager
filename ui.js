let data = null;
let project = "全部项目";
let queue = "跟进中";
let selected = null;
let inventoryVisible = false;
let catalogVisible = false;
let activity = null;
let activityStream = null;
let activityReceivedAt = 0;
let activityFailed = false;
let catalog = null;
let catalogController = null;
let catalogVersion = 0;
let catalogTimer = null;
let catalogSignature = "";
let taskSearch = "";
let catalogSearch = "";
let pending = false;
let mutating = false;
let requestVersion = 0;
let readController = null;
let noticeTimer = null;
const drafts = new Map();
const timelineDrafts = new Map();
const timelineFilters = new Map();
const detailExpansion = new Map();
const byId = identity => document.getElementById(identity);
const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const dateText = value => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "未知";
function notify(message) {
  window.clearTimeout(noticeTimer);
  byId("notice").textContent = message;
  byId("notice").hidden = false;
  noticeTimer = window.setTimeout(() => { byId("notice").hidden = true; }, 5000);
}
async function load() {
  const version = ++requestVersion;
  if (readController) readController.abort();
  const controller = new AbortController();
  readController = controller;
  pending = true;
  try {
    const response = await fetch("/api/state", { signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (version !== requestVersion) return false;
    data = result;
    render();
    return true;
  } catch (error) {
    if (version !== requestVersion || error.name === "AbortError") return false;
    notify("读取失败：" + error.message);
    return false;
  } finally {
    if (version === requestVersion) {
      pending = false;
      readController = null;
    }
  }
}
async function mutate(path, payload) {
  if (!data || mutating) return false;
  mutating = true;
  ++requestVersion;
  if (readController) readController.abort();
  pending = false;
  render();
  let saved = false;
  let rejected = false;
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Manager-Token": data.token }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) {
      rejected = true;
      if (response.status === 403) await load();
      throw new Error(result.error);
    }
    saved = true;
    if (path === "/api/notes" && drafts.get(payload.task_id)?.trim() === payload.text) drafts.delete(payload.task_id);
    if (path === "/api/timeline" && timelineDrafts.get(payload.task_id)?.id === payload.id) timelineDrafts.delete(payload.task_id);
    const refreshed = await load();
    notify(refreshed ? (path === "/api/refresh" ? "本机记录已刷新" : path === "/api/timeline" ? "阶段变化已记录；当前队列与进度摘要保持不变" : "已保存，队列和计数已更新") : "已保存，但界面刷新失败；请点“刷新”，不要重复提交。");
    return true;
  } catch (error) {
    notify((saved ? "已保存，但界面更新失败：" : rejected ? "未保存：" : "保存结果未确认，请刷新核对，不要重复提交：") + error.message);
    return false;
  } finally {
    mutating = false;
    render();
  }
}
function render() {
  byId("page-title").textContent = catalogVisible ? "Claude Code" : inventoryVisible ? "已关联聊天" : "我的任务";
  byId("search").placeholder = catalogVisible ? "搜索项目、路径或对话内容" : "搜索任务";
  byId("claude-button").classList.toggle("selected", catalogVisible);
  byId("inventory-button").classList.toggle("selected", inventoryVisible);
  byId("workspace").hidden = inventoryVisible || catalogVisible;
  byId("inventory").hidden = !inventoryVisible;
  byId("claude-catalog").hidden = !catalogVisible;
  byId("queues").hidden = inventoryVisible || catalogVisible;
  if (!data) return;
  const search = (catalogVisible ? taskSearch : byId("search").value).trim().toLowerCase();
  const view = ManagerView.deriveView(data.tasks, { project, queue, search });
  project = view.selectedProject;
  const following = data.tasks.filter(task => task.queue === "跟进中").length;
  const lastSuccess = data.auto_review?.last_success_at;
  byId("sync").textContent = `${following} 项跟进中 / ${data.tasks.length} 项任务　${lastSuccess ? `最近自动回读 ${dateText(lastSuccess)}` : "暂无自动回读记录"}${data.auto_review?.error ? ` · 检测异常：${data.auto_review.error}` : ""}`;
  byId("sync").title = `人工基线 ${data.analysis_date}；最近后台检测 ${dateText(data.auto_review?.checked_at)}。各任务已读位置独立记录，最近一次成功不代表全部同步。`;
  byId("scope").textContent = data.scope + (data.errors.length ? " 读取异常：" + data.errors.join("；") : "");
  byId("count-scope").textContent = `${queue}${search.trim() ? " · 搜索结果" : ""}`;
  byId("refresh").disabled = mutating;
  byId("auto-review-toggle").textContent = data.auto_review?.enabled ? "自动分析：开" : "自动分析：关";
  byId("auto-review-toggle").disabled = mutating || Boolean(data.demo);
  byId("auto-review-toggle").title = data.auto_review?.error || "跟进中的聊天整轮结束后，自动回读新增对话。";
  for (const button of byId("queues").querySelectorAll("button")) {
    button.textContent = `${button.dataset.queue} ${view.queueCounts[button.dataset.queue]}`;
    button.classList.toggle("selected", button.dataset.queue === queue);
  }
  byId("projects").replaceChildren();
  for (const name of view.projects) {
    const button = element("button", name, project === name && !inventoryVisible && !catalogVisible ? "selected" : "");
    button.append(element("small", String(view.projectCounts[name])));
    button.onclick = () => { project = name; if (catalogVisible) { catalogSearch = byId("search").value; byId("search").value = taskSearch; } catalogVisible = false; inventoryVisible = false; render(); };
    byId("projects").append(button);
  }
  if (catalogVisible) { renderActivity(); return; }
  if (inventoryVisible) {
    byId("coverage").textContent = `${data.entries.length} 条已关联记录，不含 Claude Code 检索结果。`;
    byId("inventory-rows").replaceChildren();
    for (const entry of data.entries.filter(entry => JSON.stringify(entry).toLowerCase().includes(search)).sort((left, right) => (right.updated || "").localeCompare(left.updated || ""))) {
      const row = element("tr");
      const title = element("td", entry.title);
      title.append(element("small", entry.id));
      if (entry.category === "聊天") {
        const assigned = data.tasks.some(task => task.source_ids.includes(entry.id));
        if (assigned) title.append(element("small", "已关联任务"));
        else {
          const follow = element("button", "加入跟进");
          follow.disabled = mutating;
          follow.onclick = () => mutate("/api/add-task", { entry_id: entry.id });
          title.append(follow);
        }
      }
      const status = element("td");
      status.append(activityBadge([entry.id]));
      row.append(title, element("td", entry.source), status, element("td", dateText(entry.updated)));
      byId("inventory-rows").append(row);
    }
    renderActivity();
    return;
  }
  const tasks = view.visibleTasks;
  byId("task-list").replaceChildren();
  if (!tasks.some(task => task.id === selected)) selected = tasks[0]?.id;
  for (const task of tasks) {
    const button = element("button", undefined, "task" + (task.id === selected ? " selected" : ""));
    const meta = element("div", undefined, "task-meta");
    meta.append(element("span", task.priority, "priority " + task.priority), activityBadge(task.source_ids));
    button.append(meta, element("h3", task.name), element("p", task.stage));
    const request = pendingRequest(task);
    if (request) {
      const preview = element("p", "最新要求：" + (request.length > 100 ? request.slice(0, 100) + "…" : request), "review");
      preview.title = "原文摘录，未交付：" + request;
      button.append(preview);
    }
    if (task.needs_review) button.append(element("span", task.auto_analysis?.label || "分析待更新", "review"));
    button.onclick = () => { selected = task.id; render(); };
    byId("task-list").append(button);
  }
  if (!tasks.length) byId("task-list").append(element("p", "这里暂无任务。可切换队列或清空搜索。", "empty"));
  const activeInput = document.activeElement;
  const editing = activeInput && activeInput.dataset.taskId === selected && activeInput.dataset.field && Array.from(byId("detail").querySelectorAll("*")).includes(activeInput);
  const selection = editing && typeof activeInput.selectionStart === "number" ? [activeInput.selectionStart, activeInput.selectionEnd] : null;
  for (const node of byId("detail").querySelectorAll("details")) detailExpansion.set(node.dataset.key, node.open);
  renderDetail(tasks.find(task => task.id === selected));
  for (const node of byId("detail").querySelectorAll("details")) node.open = Boolean(detailExpansion.get(node.dataset.key));
  if (editing) {
    const input = Array.from(byId("detail").querySelectorAll("*")).find(node => node.dataset.field === activeInput.dataset.field);
    if (input) {
      input.focus({ preventScroll: true });
      if (selection) input.setSelectionRange(...selection);
    }
  }
  renderActivity();
}
function pendingRequest(task) {
  if (!task.needs_review) return "";
  const sources = task.sources.filter(source => !source.source_stale && source.saved_turn_status?.status === "inProgress" && typeof source.latest_request === "string" && source.latest_request.trim());
  sources.sort((left, right) => (right.updated || "").localeCompare(left.updated || ""));
  return sources[0]?.latest_request.trim() || "";
}
function renderDetail(task) {
  const panel = byId("detail");
  panel.replaceChildren();
  if (!task) { panel.append(element("p", "选择一项任务查看切回卡。", "empty")); return; }
  const meta = element("div", undefined, "detail-meta");
  meta.append(element("span", `${task.project} / ${task.priority}`, "tag"), activityBadge(task.source_ids), element("span", `当前队列：${task.queue}`, "tag"));
  const request = pendingRequest(task);
  panel.append(meta, element("h2", task.name), element("p", (request ? "上轮进度：" : "") + task.stage, "stage"));
  if (request) {
    const current = element("p", undefined, "review");
    current.append(element("strong", "最新要求（未交付）："), element("span", request));
    panel.append(current);
  }
  if (task.id !== "manager" && task.auto_analysis) {
    const through = task.auto_analysis.reviewed_through;
    const label = element("p", (through ? `已分析至 ${dateText(through)} · ` : "") + task.auto_analysis.label, task.needs_review ? "review analysis-status" : "subtle analysis-status");
    label.title = task.auto_analysis.error || "已分析位置与聊天运行状态分别记录。";
    panel.append(label);
    if (task.auto_analysis.state === "failed") {
      const retry = element("button", "重试分析");
      retry.disabled = mutating;
      retry.onclick = () => mutate("/api/review-retry", { task_id: task.id });
      panel.append(retry);
    }
  } else if (task.needs_review) panel.append(element("p", "有新对话，分析待更新。", "review"));
  const actions = element("div", undefined, "actions");
  for (const source of task.sources) {
    if (source.link && /^codex:\/\/threads\/[a-z0-9-]+$/.test(source.link)) {
      const link = element("a", task.sources.length === 1 ? "打开聊天" : source.title, "open-chat");
      link.href = source.link;
      actions.append(link);
    }
  }
  for (const [target, label] of [["已完成", "我确认结项"], ["跟进中", "恢复跟进"], ["暂不跟进", "暂不跟进"]]) {
    if (target === task.queue) continue;
    const button = element("button", label, target === "已完成" ? "primary" : "");
    button.disabled = mutating;
    button.onclick = () => { if (target !== "已完成" || window.confirm("只将本总管任务标记为已完成；原聊天保持不变。确认结项？")) mutate("/api/queue", { task_id: task.id, queue: target }); };
    actions.append(button);
  }
  panel.append(actions);
  const next = element("div", undefined, "next-step");
  next.append(element("h3", task.queue === "已完成" ? "已结项" : "下一步"));
  if (task.queue === "已完成") next.append(element("p", "此任务已由你确认完成，可随时恢复跟进。"));
  else {
    if (task.needs_review) next.append(element("p", "以下是上轮建议；本轮新要求尚未回读。", "review"));
    const plan = task.next_plan;
    if (plan && typeof plan.summary === "string" && Array.isArray(plan.points) && plan.points.length && plan.points.every(point => point && typeof point.label === "string" && typeof point.text === "string")) {
      const summary = element("p", undefined, "next-summary");
      summary.append(element("strong", plan.summary));
      const points = element("ul", undefined, "next-points");
      for (const point of plan.points) {
        const item = element("li");
        item.append(element("strong", point.label + "："), element("span", point.text));
        points.append(item);
      }
      next.append(summary, points);
    } else {
      next.append(element("p", "下一步尚未整理成短要点。", "next-summary"));
      if (task.next) {
        const original = element("details");
        original.dataset.key = task.id + ":next-original";
        original.append(element("summary", "查看原下一步全文"), element("p", task.next));
        next.append(original);
      }
    }
  }
  panel.append(next);
  if (task.queue !== "已完成") {
    const confirmation = element("section", undefined, "confirmation");
    confirmation.append(element("h3", "待确认"));
    if (task.needs_review) confirmation.append(element("p", "基于上轮分析，新消息尚未核对。", "review"));
    const brief = task.confirmation_plan;
    if (brief && typeof brief.summary === "string" && Array.isArray(brief.points) && brief.points.length <= 3 && brief.points.every(point => point && typeof point.label === "string" && typeof point.text === "string")) {
      const summary = element("p", undefined, "confirmation-summary");
      summary.append(element("strong", brief.summary));
      confirmation.append(summary);
      if (brief.points.length) {
        const points = element("ul", undefined, "confirmation-points");
        for (const point of brief.points) {
          const item = element("li");
          item.append(element("strong", point.label + "："), element("span", point.text));
          points.append(item);
        }
        confirmation.append(points);
      }
    } else {
      confirmation.append(element("p", "待确认内容尚未整理成短要点。", "confirmation-summary"));
      if (task.blocker) {
        const original = element("details");
        original.dataset.key = task.id + ":confirmation-original";
        original.append(element("summary", "查看原待确认全文"), element("p", task.blocker));
        confirmation.append(original);
      }
    }
    panel.append(confirmation);
  }
  if (task.planned_branches?.length) {
    const branches = element("section", undefined, "branch-plan");
    branches.setAttribute("aria-label", "后续分支");
    branches.append(element("h3", "后续分支"), element("p", "等待你创建分支聊天；尚未关联，不计入任务数。", "subtle"));
    const list = element("ul");
    for (const branch of task.planned_branches) {
      const item = element("li");
      item.append(element("strong", branch.name), element("span", branch.focus));
      list.append(item);
    }
    branches.append(list);
    panel.append(branches);
  }
  panel.append(element("h3", "切回提醒"));
  for (const note of task.notes) {
    const node = element("div", undefined, "note" + (note.done ? " done" : ""));
    node.append(element("span", (note.pinned ? "置顶 · " : "") + note.text));
    node.append(element("br"));
    for (const [field, label] of [["pinned", note.pinned ? "取消置顶" : "置顶"], ["done", note.done ? "恢复待处理" : "标为已处理"]]) {
      const button = element("button", label);
      button.disabled = mutating;
      button.onclick = () => mutate("/api/note-state", { task_id: task.id, note_id: note.id, field, value: !Boolean(note[field]) });
      node.append(button);
    }
    panel.append(node);
  }
  const form = element("form", undefined, "note-form");
  const input = element("textarea");
  input.dataset.taskId = task.id;
  input.dataset.field = "note";
  input.value = drafts.get(task.id) || "";
  input.oninput = () => drafts.set(task.id, input.value);
  input.placeholder = "记下下次要做的事…";
  input.rows = 2;
  input.setAttribute("aria-label", "追加切回提醒");
  input.maxLength = 3000;
  const save = element("button", "追加提醒");
  save.type = "submit";
  save.disabled = mutating;
  form.append(input, save);
  form.onsubmit = event => { event.preventDefault(); if (input.value.trim()) mutate("/api/notes", { task_id: task.id, text: input.value.trim() }); };
  panel.append(form);
  panel.append(renderTimeline(task));
  const analysis = element("details", undefined, "analysis-detail");
  analysis.dataset.key = task.id + ":analysis";
  analysis.append(element("summary", "完整分析与依据"), element("p", `分析基线 ${data.analysis_date}；${task.confidence}`, "confidence"));
  for (const [label, key] of [["目标", "goal"], ["进展", "progress"], ["待确认", "blocker"], ["建议下一步", "next"]]) {
    analysis.append(element("h3", label), element("p", task[key]));
  }
  const evidence = element("details");
  evidence.dataset.key = task.id + ":evidence";
  evidence.append(element("summary", "查看依据与容易遗漏的事项"));
  const list = element("ul", undefined, "evidence");
  for (const text of [...task.evidence, ...task.cautions]) list.append(element("li", text));
  evidence.append(list);
  analysis.append(evidence, element("h3", "关联原聊天"));
  for (const source of task.sources) {
    const node = element("div", undefined, "source");
    if (source.link && /^codex:\/\/threads\/[a-z0-9-]+$/.test(source.link)) {
      const link = element("a", source.title);
      link.href = source.link;
      node.append(link);
    } else node.append(element("strong", source.title));
    node.append(element("p", `${source.source} · ${dateText(source.updated)}`), element("p", source.runtime || "无即时状态"));
    if (source.source_stale) node.append(element("p", `来源暂不可读，保留上次记录；首次缺失：${dateText(source.missing_since_scan)}。结项和提醒不受影响。`, "review"));
    if (source.route) node.append(element("p", `路由最后HTTP ${source.route.last_status}；仅代表一次请求，不代表任务完成。`));
    const messages = element("details");
    messages.dataset.key = task.id + ":" + source.id;
    messages.append(element("summary", "最新保存的请求与回应"), element("pre", `请求：\n${source.latest_request || "未记录正文"}\n\n回应：\n${source.latest_response || "暂无回应"}\n\n源记录：${source.original_path || source.path || source.id}`));
    node.append(messages);
    analysis.append(node);
  }
  panel.append(analysis);
}
function renderTimeline(task) {
  const section = element("section", undefined, "timeline");
  section.setAttribute("aria-label", "项目时间线");
  const events = task.timeline || [];
  const heading = element("div", undefined, "timeline-heading");
  heading.append(element("h3", "项目时间线"), element("span", `${events.length} 个节点`, "subtle"));
  section.append(heading, element("p", "关注点如何变化 · 最近在前", "subtle"));
  if (task.needs_review) section.append(element("p", data.auto_review?.enabled ? "后续有新消息，整轮结束后会自动补记阶段变化。" : "有新对话待回读，时间线尚未覆盖新增进展。", "review"));
  const focuses = [...new Set(events.map(event => event.focus || "未分类"))];
  let chosen = timelineFilters.get(task.id) || "";
  if (!focuses.includes(chosen)) chosen = "";
  timelineFilters.set(task.id, chosen);
  if (focuses.length > 1) {
    const filter = element("select", undefined, "timeline-filter");
    filter.dataset.taskId = task.id;
    filter.dataset.field = "timeline-filter";
    filter.setAttribute("aria-label", "筛选时间线关注点");
    for (const focus of ["", ...focuses]) {
      const option = element("option", focus || "全部关注点");
      option.value = focus;
      filter.append(option);
    }
    filter.value = chosen;
    filter.onchange = () => { timelineFilters.set(task.id, filter.value); render(); };
    section.append(filter);
  }
  const visible = events.filter(event => !chosen || (event.focus || "未分类") === chosen);
  const list = element("ol", undefined, "timeline-list");
  for (const event of visible.slice(0, 4)) list.append(timelineNode(task, event));
  section.append(list);
  if (!visible.length) section.append(element("p", "还没有阶段记录。可以补记一个关注点，或回总管聊天要求回读整理。", "empty"));
  if (visible.length > 4) {
    const history = element("details", undefined, "timeline-history");
    history.dataset.key = `${task.id}:timeline-history:${chosen}`;
    history.append(element("summary", `展开较早的 ${visible.length - 4} 个节点`));
    const earlier = element("ol", undefined, "timeline-list");
    for (const event of visible.slice(4)) earlier.append(timelineNode(task, event));
    history.append(earlier);
    section.append(history);
  }
  const addition = element("details", undefined, "timeline-add");
  addition.dataset.key = task.id + ":timeline-add";
  addition.append(element("summary", "＋ 补记阶段变化"));
  const form = element("form", undefined, "timeline-form");
  if (!timelineDrafts.has(task.id)) timelineDrafts.set(task.id, { id: crypto.randomUUID(), kind: "实现", occurred_on: new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" }), focus: "", title: "", detail: "" });
  const draft = timelineDrafts.get(task.id);
  for (const [field, label, tag, limit] of [["title", "节点标题", "input", 80], ["focus", "关注点", "input", 40], ["kind", "节点类型", "select"], ["occurred_on", "发生日期", "input"], ["detail", "变化与结果", "textarea", 1200]]) {
    const wrapper = element("label", label, "timeline-field timeline-field-" + field);
    const input = element(tag);
    input.dataset.taskId = task.id;
    input.dataset.field = "timeline-" + field;
    input.setAttribute("aria-label", label);
    if (field === "kind") for (const kind of ["想法", "调研", "决策", "实现", "验证", "阻塞"]) {
      const option = element("option", kind);
      option.value = kind;
      input.append(option);
    }
    if (field === "occurred_on") { input.type = "date"; input.max = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" }); }
    else if (tag === "input") input.type = "text";
    if (limit) input.maxLength = limit;
    if (field === "title") input.placeholder = "一句短结论，例如：接入完成，面板待验";
    if (field === "focus") input.placeholder = "例如：登录体验（选填）";
    if (field === "detail") { input.placeholder = "变化：改走 Responses。\n结果：Read 闭环通过。\n待办：验收 VS Code 面板。"; input.rows = 3; }
    input.required = field !== "focus";
    input.value = draft[field];
    input.oninput = () => timelineDrafts.set(task.id, { ...timelineDrafts.get(task.id), [field]: input.value, id: crypto.randomUUID() });
    wrapper.append(input);
    form.append(wrapper);
  }
  form.append(element("p", "建议写 1–3 行“标签：短句”，每句不超过 48 字；只填有内容的项。补记不自动结项。", "subtle timeline-help"));
  const submit = element("button", "保存节点");
  submit.type = "submit";
  submit.disabled = mutating;
  form.append(submit);
  form.onsubmit = event => { event.preventDefault(); mutate("/api/timeline", { ...timelineDrafts.get(task.id), task_id: task.id }); };
  addition.append(form);
  section.append(addition);
  return section;
}
function timelineNode(task, event) {
  const node = element("li", undefined, "timeline-node");
  const meta = element("div", undefined, "timeline-meta");
  const time = element("time", event.precision === "day" ? `${event.occurred_at} · 日期补记` : new Date(event.occurred_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }));
  time.setAttribute("datetime", event.occurred_at);
  meta.append(time, element("span", event.kind), element("span", event.focus || "未分类"));
  const heading = element("h4");
  heading.append(element("strong", event.summary || event.title));
  node.append(heading, meta);
  const points = event.points || [];
  if (points.length) {
    const list = element("ul", undefined, "timeline-points");
    for (const point of points) {
      const item = element("li");
      item.append(element("strong", point.label + "："), element("span", point.text));
      list.append(item);
    }
    node.append(list);
  } else node.append(element("p", event.detail, "timeline-outcome"));
  const evidence = element("details", undefined, "timeline-evidence");
  evidence.dataset.key = task.id + ":milestone:" + event.id;
  evidence.append(element("summary", { review: "原文与依据", manual: "补记原文", queue: "操作记录" }[event.origin] || "原文与依据"));
  if (points.length) evidence.append(element("p", event.detail, "timeline-original"));
  evidence.append(element("p", `${event.origin === "review" ? "节点时间取来源消息保存时间，不代表整阶段起止。回读记录：" : "写入总管："}${event.recorded_at}`, "subtle"));
  for (const reference of event.evidence || []) {
    const source = task.sources.find(source => source.id === reference.source_id);
    evidence.append(element("p", `${reference.text}（消息序号 ${reference.ordinal}）`, "subtle"));
    if (source?.link && /^codex:\/\/threads\/[a-z0-9-]+$/.test(source.link)) {
      const link = element("a", `打开原聊天：${source.title}`);
      link.href = source.link;
      evidence.append(link);
    } else evidence.append(element("p", reference.source_id, "subtle"));
  }
  node.append(evidence);
  return node;
}
function activityBadge(identities) {
  const badge = element("span", "状态未知", "activity unknown");
  badge.dataset.sources = JSON.stringify(identities);
  return badge;
}
function renderActivity() {
  const age = activity ? Date.now() - Date.parse(activity.checked_at) : Infinity;
  const recent = age >= -5000 && age < 25000;
  const checked = activity ? new Date(activity.checked_at).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "";
  const connected = recent && !activityFailed && activity?.gateway?.connected;
  byId("activity-sync").textContent = data?.demo ? "演示模式 · 虚构内容 · 未读取本机聊天" : activityFailed || (activity && !recent) ? "状态同步中断 · 正在重连" : activity ? `${connected ? "猫老大已连接 · 实时订阅" : "猫老大未连接 · 仅本机记录"} · ${checked}${activity.errors.length ? " · 部分来源不可用" : ""}` : "正在连接猫老大…";
  byId("activity-sync").title = connected ? "Codex 与 Claude 共用猫老大增量订阅；仅反映模型请求，不代表整个任务完成。" : activity?.gateway?.error || "猫老大不可用时不会继续显示旧的实时请求。";
  const live = byId("claude-live");
  const liveRequests = connected ? activity.live_requests || [] : [];
  const liveKey = JSON.stringify([Boolean(connected), liveRequests, catalogSignature]);
  if (live.dataset.snapshot !== liveKey) {
    live.dataset.snapshot = liveKey;
    live.replaceChildren();
    live.append(element("strong", connected ? (liveRequests.length ? `Claude 当前有 ${liveRequests.length} 个模型请求` : "猫老大当前没有在途 Claude 请求") : "猫老大未连接，无法确认实时请求"));
    for (const request of liveRequests) {
      const session = catalog?.projects.flatMap(project => project.sessions).find(session => session.id === request.source_id);
      live.append(element("p", `${session?.title || request.folder || "尚未落盘的聊天"} · ${request.model || "模型未记录"} · ${request.label}${request.source_id ? "" : "（未绑定会话，不关联到项目）"}`));
    }
    live.append(element("p", "状态跟随猫老大；历史内容按需检索。没有模型请求，也可能正在执行工具或等待输入。", "subtle"));
  }
  for (const badge of document.querySelectorAll(".activity")) {
    const states = JSON.parse(badge.dataset.sources).map(identity => activity?.statuses[identity]).filter(Boolean);
    const order = ["running", "error", "paused", "unknown", "idle"];
    states.sort((left, right) => order.indexOf(left.kind) - order.indexOf(right.kind));
    const status = states[0];
    badge.textContent = activityFailed || !recent ? "状态不可用" : status?.label || "状态未知";
    badge.className = "activity " + (!activityFailed && recent ? status?.kind || "unknown" : "unknown");
    badge.title = !activityFailed && recent && status ? `${status.basis}\n记录时间：${dateText(status.updated_at)}` : "未取得可用的近期状态；不表示任务已结束。";
  }
}
function stopActivity() {
  if (activityStream) activityStream.close();
  activityStream = null;
  activity = null;
  activityFailed = true;
  renderActivity();
}
function startActivity() {
  if (document.hidden || activityStream) return;
  activityReceivedAt = Date.now();
  try {
    const stream = new EventSource("/api/activity/events");
    activityStream = stream;
    stream.addEventListener("activity", event => {
      if (activityStream !== stream) return;
      try {
        const result = JSON.parse(event.data);
        if (!result || !result.statuses || !Array.isArray(result.errors) || !Number.isFinite(Date.parse(result.checked_at))) throw new Error("状态格式不兼容");
        activity = result;
        activityFailed = false;
        activityReceivedAt = Date.now();
        if (Number.isInteger(result.analysis_revision) && result.analysis_revision !== data?.auto_review?.revision && !pending && !mutating) load();
      } catch (error) { activity = null; activityFailed = true; }
      renderActivity();
    });
    stream.onerror = () => {
      if (activityStream !== stream) return;
      activity = null;
      activityFailed = true;
      renderActivity();
    };
  } catch (error) { activityFailed = true; renderActivity(); }
}
async function loadCatalog() {
  window.clearTimeout(catalogTimer);
  catalogTimer = null;
  if (catalogController) catalogController.abort();
  const version = ++catalogVersion;
  const controller = new AbortController();
  catalogController = controller;
  byId("claude-summary").textContent = "正在检索本机 Claude Code 项目…";
  const query = byId("search").value.trim();
  const timeout = window.setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch("/api/claude-projects?q=" + encodeURIComponent(query), { signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (version !== catalogVersion) return;
    catalog = result;
    const signature = JSON.stringify([query, result.projects, result.project_count, result.session_count, result.matched_count, result.errors, result.available]);
    if (signature !== catalogSignature) {
      catalogSignature = signature;
      renderCatalog();
    } else updateCatalogSummary();
  } catch (error) {
    if (version === catalogVersion) byId("claude-summary").textContent = "检索失败，请点刷新重试；保留的结果可能已过期：" + error.message;
  } finally {
    window.clearTimeout(timeout);
  }
}
function updateCatalogSummary() {
  byId("claude-summary").textContent = `${catalog.project_count} 个项目 / ${catalog.session_count} 条聊天，匹配 ${catalog.matched_count} 条。历史按需检索 · 状态实时订阅` + (catalog.errors.length ? ` ${catalog.errors.length} 处读取异常：${catalog.errors.join("；")}` : "");
}
function renderCatalog() {
  const expanded = new Set(Array.from(byId("claude-results").querySelectorAll("details[open]")).map(node => node.dataset.key));
  byId("claude-results").replaceChildren();
  updateCatalogSummary();
  for (const project of catalog.projects) {
    const section = element("section", undefined, "catalog-project");
    section.append(element("h2", `${project.name} (${project.sessions.length}/${project.session_count})`), element("p", project.path || "项目目录未记录", "catalog-path"));
    for (const session of project.sessions) {
      const row = element("article", undefined, "session");
      const heading = element("div", undefined, "session-heading");
      const updated = element("span", dateText(session.updated), "tag");
      updated.title = session.time_basis || "最近记录";
      heading.append(element("strong", session.title), activityBadge([session.id]), updated);
      const resume = element("details");
      resume.dataset.key = session.id;
      resume.open = expanded.has(session.id);
      resume.append(element("summary", "继续此聊天"), element("p", "在终端执行以下命令；不会由总管自动运行。", "subtle"), element("pre", session.resume_command), element("p", "对话片段（非进度分析）", "subtle"), element("pre", session.preview));
      row.append(heading, element("p", session.preview, "session-preview"), resume);
      section.append(row);
    }
    byId("claude-results").append(section);
  }
  if (!catalog.projects.length) byId("claude-results").append(element("p", catalog.available ? "没有匹配的聊天。试试项目名、路径或对话关键词。" : "未找到本机 Claude Code 项目目录。", "empty"));
  renderActivity();
}
byId("search").oninput = () => {
  if (!catalogVisible) { taskSearch = byId("search").value; render(); return; }
  ++catalogVersion;
  if (catalogController) catalogController.abort();
  window.clearTimeout(catalogTimer);
  byId("claude-summary").textContent = "正在检索…";
  byId("claude-results").replaceChildren();
  catalogSignature = "";
  catalogTimer = window.setTimeout(() => { catalogTimer = null; loadCatalog(); }, 250);
};
for (const button of byId("queues").querySelectorAll("button")) button.onclick = () => {
  queue = button.dataset.queue;
  render();
};
byId("inventory-button").onclick = () => { if (catalogVisible) byId("search").value = taskSearch; catalogVisible = false; inventoryVisible = !inventoryVisible; render(); };
byId("claude-button").onclick = () => {
  if (!catalogVisible) { taskSearch = byId("search").value; byId("search").value = catalogSearch; }
  catalogVisible = true;
  inventoryVisible = false;
  render();
  loadCatalog();
};
byId("refresh").onclick = async () => {
  if (catalogVisible) await loadCatalog();
  else if (!data) await load();
  else await mutate("/api/refresh", {});
  if (!activityStream) startActivity();
};
byId("auto-review-toggle").onclick = () => data && mutate("/api/auto-review", { enabled: !data.auto_review?.enabled });
load();
startActivity();
window.setInterval(() => { if (!document.hidden && !mutating && !pending) load(); }, 15000);
window.setInterval(() => {
  if (document.hidden) return;
  renderActivity();
  if (activityStream && Date.now() - activityReceivedAt >= 25000) stopActivity();
  if (!activityStream) startActivity();
}, 1000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopActivity();
  else startActivity();
});
window.addEventListener("pagehide", stopActivity);
window.addEventListener("pageshow", startActivity);
