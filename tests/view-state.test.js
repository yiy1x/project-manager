const assert = require("node:assert/strict");
const { test } = require("node:test");
const { deriveView } = require("../view-state.js");

const tasks = [
  { id: "environment", project: "开发环境", name: "模型身份", queue: "跟进中" },
  { id: "pet", project: "桌面体验", name: "桌面宠物", queue: "跟进中" },
  { id: "manager", project: "项目总管", name: "项目主管", queue: "跟进中" },
];

test("同项目四项任务，结项一个分支不影响其余任务", () => {
  const frontier = ["outline", "research", "build", "verify"].map(id => ({ id, name: id, project: "示例项目", queue: "跟进中" }));
  const view = deriveView(frontier, { project: "示例项目", queue: "跟进中" });
  assert.equal(view.projectCounts["示例项目"], 4);
  assert.equal(view.visibleTasks.length, 4);
  const completed = frontier.map(task => task.id === "research" ? { ...task, queue: "已完成" } : task);
  const following = deriveView(completed, { project: "示例项目", queue: "跟进中" });
  assert.equal(following.projectCounts["示例项目"], 3);
  assert.deepEqual(following.visibleTasks.map(task => task.id), ["outline", "build", "verify"]);
  assert.equal(deriveView(completed, { project: "示例项目", queue: "已完成" }).projectCounts["示例项目"], 1);
});

test("结项后左侧隐藏零值项目，恢复跟进后重新显示，任务总数保留", () => {
  for (const queue of ["跟进中", "已完成", "跟进中", "暂不跟进"]) {
    const current = tasks.map(task => task.id === "pet" ? { ...task, queue } : task);
    const view = deriveView(current, { project: "全部项目", queue: "跟进中" });
    assert.equal(view.projectCounts["桌面体验"], Number(queue === "跟进中"));
    assert.equal(view.projectCounts["全部项目"], queue === "跟进中" ? 3 : 2);
    assert.equal(view.projectCounts["全部项目"], view.visibleTasks.length);
    assert.equal(view.queueCounts[queue], queue === "跟进中" ? 3 : 1);
    assert.equal(view.projects.includes("桌面体验"), queue === "跟进中");
    assert.equal(current.length, 3);
  }
});

test("项目、队列、搜索交叉筛选时各计数与对应列表一致", () => {
  const current = tasks.map(task => task.id === "pet" ? { ...task, queue: "已完成" } : task);
  for (const project of ["全部项目", "开发环境", "桌面体验", "项目总管"]) {
    for (const queue of ["跟进中", "已完成", "暂不跟进"]) {
      for (const search of ["", " 桌面宠物 ", "不存在"]) {
        const view = deriveView(current, { project, queue, search });
        assert.equal(view.projectCounts[view.selectedProject], view.visibleTasks.length);
        assert.equal(view.queueCounts[queue], view.visibleTasks.length);
        for (const target of view.projects) {
          assert.ok(target === "全部项目" || view.projectCounts[target] > 0);
          assert.equal(view.projectCounts[target], deriveView(current, { project: target, queue, search }).visibleTasks.length);
        }
      }
    }
  }
});

test("全部结项时跟进中全部归零，已完成仍有全部任务", () => {
  const completed = tasks.map(task => ({ ...task, queue: "已完成" }));
  const view = deriveView(completed, { queue: "跟进中" });
  assert.deepEqual(Object.values(view.projectCounts), [0, 0, 0, 0]);
  assert.deepEqual(view.projects, ["全部项目"]);
  assert.equal(view.queueCounts["已完成"], 3);
  assert.equal(deriveView(completed, { queue: "已完成" }).visibleTasks.length, 3);
  assert.deepEqual(deriveView([], { queue: "跟进中" }).projectCounts, { 全部项目: 0 });
});

test("每个队列独立展示非零项目，隐藏当前项目时统一回到全部项目", () => {
  const current = [...tasks, { id: "old-pet", project: "桌面体验", name: "旧宠物", queue: "已完成" }, { id: "paused", project: "暂停项目", queue: "暂不跟进" }];
  const completed = deriveView(current, { queue: "已完成", project: "开发环境" });
  assert.deepEqual(completed.projects, ["全部项目", "桌面体验"]);
  assert.equal(completed.selectedProject, "全部项目");
  assert.deepEqual(completed.visibleTasks.map(task => task.id), ["old-pet"]);
  assert.equal(completed.queueCounts["已完成"], 1);
  const paused = deriveView(current, { queue: "暂不跟进" });
  assert.deepEqual(paused.projects, ["全部项目", "暂停项目"]);
  const searching = deriveView(current, { queue: "跟进中", project: "开发环境", search: "桌面宠物" });
  assert.deepEqual(searching.projects, ["全部项目", "桌面体验"]);
  assert.equal(searching.selectedProject, "全部项目");
  assert.deepEqual(searching.visibleTasks.map(task => task.id), ["pet"]);
});
