(function (root) {
  function selectTasks(tasks, { project = "全部项目", queue = null, search = "" } = {}) {
    const query = search.trim().toLowerCase();
    return tasks.filter(task => (!queue || task.queue === queue) && (project === "全部项目" || task.project === project) && JSON.stringify(task).toLowerCase().includes(query));
  }

  function deriveView(tasks, filters = {}) {
    const allProjects = ["全部项目", ...new Set(tasks.map(task => task.project))];
    const projectCounts = Object.fromEntries(allProjects.map(project => [project, selectTasks(tasks, { ...filters, project }).length]));
    const projects = allProjects.filter(project => project === "全部项目" || projectCounts[project] > 0);
    const selectedProject = projects.includes(filters.project) ? filters.project : "全部项目";
    const selection = { ...filters, project: selectedProject };
    const visibleTasks = selectTasks(tasks, selection);
    const queueCounts = Object.fromEntries(["跟进中", "已完成", "暂不跟进"].map(queue => [queue, selectTasks(tasks, { ...selection, queue }).length]));
    return { projects, selectedProject, visibleTasks, projectCounts, queueCounts };
  }

  root.ManagerView = { selectTasks, deriveView };
  if (typeof module !== "undefined") module.exports = root.ManagerView;
})(typeof window === "undefined" ? globalThis : window);
