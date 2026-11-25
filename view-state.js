(function (root) {
  function timestamp(value, clock = Date.now()) {
    const result = typeof value === "number" ? (value < 1e11 ? value * 1000 : value) : typeof value === "string" && value.trim() ? Date.parse(value) : NaN;
    return Number.isFinite(result) && result > 0 && result <= clock + 5000 ? result : 0;
  }

  function liveStatuses(activity, failed = false, clock = Date.now()) {
    const checked = timestamp(activity?.checked_at, clock);
    if (failed || !checked || clock - checked >= 25000) return {};
    return Object.fromEntries(Object.entries(activity.statuses || {}).filter(([, status]) => {
      if (!status || (status.raw === "gateway" && !activity.gateway?.connected)) return false;
      if (status.kind === "running" && status.raw !== "gateway" && status.updated_at != null) {
        const updated = timestamp(status.updated_at, clock);
        return updated && clock - updated <= 120000;
      }
      return true;
    }));
  }

  function sourceRank(source, statuses, clock) {
    const status = statuses[source.id];
    const running = status?.kind === "running";
    const saved = source.saved_turn_status || {};
    const ended = timestamp(status?.completed_at, clock) || (!source.source_stale && ["completed", "failed", "interrupted"].includes(saved.status) ? timestamp(saved.completed_at, clock) : 0);
    const updated = Math.max(timestamp(source.updated, clock), status?.raw === "gateway" ? 0 : timestamp(status?.updated_at, clock));
    const started = timestamp(status?.started_at, clock) || timestamp(saved.started_at, clock);
    return { running: Boolean(running), time: running ? started || updated : ended || updated };
  }

  function compareRanks(left, right) {
    return Number(right.running) - Number(left.running) || right.time - left.time;
  }

  function bestRank(ranks) {
    return ranks.reduce((best, rank) => compareRanks(rank, best) < 0 ? rank : best, { running: false, time: 0 });
  }

  function taskRank(task, statuses, clock = Date.now()) {
    const sources = task.sources || [];
    const identities = new Set([...(task.source_ids || []), ...sources.map(source => source.id)]);
    return bestRank([...identities].map(id => sourceRank(sources.find(source => source.id === id) || { id }, statuses, clock)));
  }

  function sortByRank(items, rank) {
    return items.map((item, index) => ({ item, index, rank: rank(item) })).sort((left, right) => compareRanks(left.rank, right.rank) || left.index - right.index).map(entry => entry.item);
  }

  function sortSources(sources, statuses = {}, clock = Date.now()) {
    return sortByRank(sources, source => sourceRank(source, statuses, clock));
  }

  function sortCatalog(projects, statuses = {}, clock = Date.now()) {
    return sortByRank(projects.map(project => ({ ...project, sessions: sortSources(project.sessions, statuses, clock) })), project => bestRank(project.sessions.map(session => sourceRank(session, statuses, clock))));
  }

  function selectTasks(tasks, { project = "全部项目", queue = null, search = "" } = {}) {
    const query = search.trim().toLowerCase();
    return tasks.filter(task => (!queue || task.queue === queue) && (project === "全部项目" || task.project === project) && JSON.stringify(task).toLowerCase().includes(query));
  }

  function deriveView(tasks, filters = {}, statuses = {}, clock = Date.now()) {
    const allProjects = ["全部项目", ...new Set(tasks.map(task => task.project))];
    const projectCounts = Object.fromEntries(allProjects.map(project => [project, selectTasks(tasks, { ...filters, project }).length]));
    const projectNames = allProjects.filter(project => project !== "全部项目" && projectCounts[project] > 0);
    const projects = ["全部项目", ...sortByRank(projectNames, project => bestRank(selectTasks(tasks, { ...filters, project }).map(task => taskRank(task, statuses, clock))))];
    const selectedProject = projects.includes(filters.project) ? filters.project : "全部项目";
    const selection = { ...filters, project: selectedProject };
    const visibleTasks = sortByRank(selectTasks(tasks, selection), task => taskRank(task, statuses, clock));
    const queueCounts = Object.fromEntries(["跟进中", "已完成", "暂不跟进"].map(queue => [queue, selectTasks(tasks, { ...selection, queue }).length]));
    return { projects, selectedProject, visibleTasks, projectCounts, queueCounts };
  }

  root.ManagerView = { selectTasks, deriveView, timestamp, liveStatuses, sortSources, sortCatalog };
  if (typeof module !== "undefined") module.exports = root.ManagerView;
})(typeof window === "undefined" ? globalThis : window);
