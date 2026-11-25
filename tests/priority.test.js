const assert = require("node:assert/strict");
const { test } = require("node:test");
const { deriveView, liveStatuses, sortSources, sortCatalog } = require("../view-state.js");

const clock = Date.parse("2026-11-25T12:00:00+08:00");
const stamp = seconds => new Date(clock - seconds * 1000).toISOString();
const source = (id, seconds, extra = {}) => ({ id, updated: stamp(seconds), ...extra });
const task = (id, seconds, extra = {}) => ({ id, name: id, project: id, queue: "跟进中", source_ids: [id], sources: [source(id, seconds)], ...extra });
const activity = statuses => ({ checked_at: stamp(0), gateway: { connected: true }, statuses });

test("进行中在前，其余按真实结束倒序而非元数据或心跳更新时间", () => {
  const tasks = [task("recent", 1), task("old", 2), task("running", 80)];
  const statuses = {
    recent: { kind: "idle", completed_at: stamp(20) },
    old: { kind: "idle", completed_at: stamp(300), updated_at: stamp(0), raw: "gateway" },
    running: { kind: "running", started_at: stamp(100) },
  };
  const before = JSON.stringify(tasks);
  const view = deriveView(tasks, { queue: "跟进中" }, statuses, clock);
  assert.deepEqual(view.visibleTasks.map(item => item.id), ["running", "recent", "old"]);
  assert.deepEqual(view.projects, ["全部项目", "running", "recent", "old"]);
  assert.equal(JSON.stringify(tasks), before);
  assert.equal(view.queueCounts["已完成"], 0);
});

test("状态断线、过期和单来源过期不得继续置顶", () => {
  const record = activity({ run: { kind: "running", updated_at: stamp(1) }, stale: { kind: "running", updated_at: stamp(121) } });
  assert.deepEqual(Object.keys(liveStatuses(record, false, clock)), ["run"]);
  assert.deepEqual(liveStatuses(record, true, clock), {});
  assert.deepEqual(liveStatuses({ ...record, checked_at: stamp(25) }, false, clock), {});
  assert.deepEqual(liveStatuses({ ...record, checked_at: stamp(-6) }, false, clock), {});
  assert.deepEqual(liveStatuses({ ...record, gateway: { connected: false }, statuses: { run: { kind: "running", raw: "gateway" } } }, false, clock), {});
});

test("无实时依据不从保存的进行中字段猜心跳，结束秒毫秒和ISO兼容", () => {
  const entries = [source("stale", 500, { saved_turn_status: { status: "inProgress" } }), source("ended", 900, { saved_turn_status: { status: "completed", completed_at: (clock - 20000) / 1000 } }), source("failed", 800, { saved_turn_status: { status: "failed", completed_at: clock - 10000 } })];
  assert.deepEqual(sortSources(entries, {}, clock).map(item => item.id), ["failed", "ended", "stale"]);
});

test("没有结束记录回退最近记录，未知时间稳定排序且忽略未来时间", () => {
  const entries = [{ id: "empty" }, { id: "bad", updated: "bad" }, source("future", -100), source("recorded", 50)];
  assert.deepEqual(sortSources(entries, {}, clock).map(item => item.id), ["recorded", "empty", "bad", "future"]);
  assert.deepEqual(sortSources(entries, { empty: { raw: "gateway", kind: "idle", updated_at: stamp(0) } }, clock).map(item => item.id), ["recorded", "empty", "bad", "future"]);
});

test("聚合项目由匹配筛选的最高优先任务决定，零值项目仍隐藏", () => {
  const tasks = [task("running", 90, { project: "mixed", queue: "暂不跟进" }), task("old", 70, { project: "mixed" }), task("new", 20, { project: "other" })];
  const statuses = { running: { kind: "running" } };
  assert.deepEqual(deriveView(tasks, { queue: "跟进中" }, statuses, clock).projects, ["全部项目", "other", "mixed"]);
  const paused = deriveView(tasks, { project: "other", queue: "暂不跟进" }, statuses, clock);
  assert.deepEqual(paused.projects, ["全部项目", "mixed"]);
  assert.equal(paused.selectedProject, "全部项目");
  assert.equal(paused.visibleTasks[0].queue, "暂不跟进");
});

test("多来源任务任一有效来源进行中就置顶，项目和Claude复用同一排序", () => {
  const entries = [source("old", 500), source("active", 300), source("new", 20)];
  const statuses = { active: { kind: "running", started_at: stamp(100) } };
  const tasks = [task("new", 20), task("multi", 800, { sources: entries.slice(0, 2), source_ids: ["old", "active"] })];
  assert.equal(deriveView(tasks, {}, statuses, clock).visibleTasks[0].id, "multi");
  const projects = [{ id: "ordinary", sessions: [entries[2]] }, { id: "active-group", sessions: entries.slice(0, 2) }];
  const sorted = sortCatalog(projects, statuses, clock);
  assert.deepEqual(sorted.map(group => group.id), ["active-group", "ordinary"]);
  assert.deepEqual(sorted[0].sessions.map(entry => entry.id), ["active", "old"]);
  assert.equal(projects[1].sessions[0].id, "old");
});

test("并行中的任务按开始时间稳定排序，结束后回到结束队列", () => {
  const entries = [source("first", 500), source("second", 400), source("done", 30)];
  const statuses = { first: { kind: "running", started_at: stamp(80) }, second: { kind: "running", started_at: stamp(40) }, done: { kind: "idle", completed_at: stamp(20) } };
  assert.deepEqual(sortSources(entries, statuses, clock).map(entry => entry.id), ["second", "first", "done"]);
  statuses.second = { kind: "idle", completed_at: stamp(1) };
  assert.deepEqual(sortSources(entries, statuses, clock).map(entry => entry.id), ["first", "second", "done"]);
});
