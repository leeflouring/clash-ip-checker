(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const terminalStatuses = new Set(["completed", "cancelled", "error"]);
  const statusLabels = {
    idle: "等待任务",
    queued: "排队中",
    running: "检测中",
    reconnecting: "连接恢复中",
    cancelled: "已取消",
    error: "任务出错",
    unknown: "任务已过期",
    completed: "检测完成",
  };
  const rowStatusLabels = {
    pending: "等待",
    checked: "完成",
    failed: "异常",
    skipped: "跳过",
  };

  const state = {
    jobId: null,
    status: "idle",
    rows: new Map(),
    selected: new Set(),
    eventSource: null,
    reconnects: 0,
    reconnectTimer: null,
    editingId: null,
    busyIds: new Set(),
    raw: "",
    view: "table",
    apiToken: "",
    pollTimer: null,
    historyMode: false,
    historyViewedId: null,
    sort: {
      key: "id",
      direction: "asc",
    },
    filters: {
      name: "",
      type: "",
      native: "",
      source: "",
      status: "",
    },
  };

  const elements = {
    form: $("#start-form"),
    formError: $("#form-error"),
    workspace: $("#workspace"),
    rows: $("#rows"),
    tableWrap: $("#table-wrap"),
    emptyState: $("#empty-state"),
    selectAll: $("#select-all"),
    filterName: $("#filter-name"),
    filterType: $("#filter-type"),
    filterNative: $("#filter-native"),
    filterSource: $("#filter-source"),
    filterStatus: $("#filter-status"),
    selectedCount: $("#selected-count"),
    state: $("#state"),
    jobLabel: $("#job-label"),
    progressCount: $("#progress-count"),
    progressMessage: $("#progress-message"),
    progressBar: $("#progress-bar"),
    rawPanel: $("#raw-panel"),
    rawView: $("#raw-view"),
    tableView: $("#table-view"),
    cancel: $("#cancel"),
    toast: $("#toast"),
    historyList: $("#history-list"),
    historyStatus: $("#history-status"),
    refreshHistory: $("#refresh-history"),
    historyReadonly: $("#history-readonly"),
    returnCurrent: $("#return-current"),
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(
      /[&<>"']/g,
      (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
    );
  }

  function rememberJob(jobId) {
    try {
      if (jobId) {
        sessionStorage.setItem("active_job_id", jobId);
      } else {
        sessionStorage.removeItem("active_job_id");
      }
    } catch {
      // Storage can be disabled; the active page still works without it.
    }
  }

  function recalledJob() {
    try {
      return sessionStorage.getItem("active_job_id");
    } catch {
      return null;
    }
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.apiToken) {
      headers.set("Authorization", `Bearer ${state.apiToken}`);
    }
    const response = await fetch(path, { ...options, headers });
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const message = body && typeof body === "object" ? body.detail : body;
      throw new Error(message || `请求失败（${response.status}）`);
    }
    return body;
  }

  let toastTimer;
  function showToast(message) {
    clearTimeout(toastTimer);
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    toastTimer = setTimeout(() => {
      elements.toast.hidden = true;
    }, 3200);
  }

  function showFormError(message = "") {
    elements.formError.textContent = message;
  }

  function setStatus(status, message) {
    state.status = status;
    elements.state.textContent = statusLabels[status] || status;
    if (message) {
      elements.progressMessage.textContent = message;
    }
  }

  function isTerminal() {
    return terminalStatuses.has(state.status);
  }

  function setSubmitDisabled(disabled) {
    elements.form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = disabled;
    });
    elements.cancel.disabled = !disabled;
  }

  function riskLevel(value) {
    const score = Number.parseFloat(String(value ?? "").replace("%", ""));
    if (!Number.isFinite(score)) return "";
    if (score >= 70) return "risk-high";
    if (score >= 35) return "risk-medium";
    return "";
  }

  function sharedOrBot(row) {
    if (row.shared && row.shared !== "N/A") return row.shared;
    if (row.bot && row.bot !== "N/A") return `Bot ${row.bot}`;
    return "—";
  }

  function updateStats() {
    const rows = [...state.rows.values()];
    const checked = rows.filter((row) => row.status !== "pending").length;
    const risky = rows.filter((row) => riskLevel(row.risk) === "risk-high").length;
    const failed = rows.filter((row) => row.status === "failed").length;
    $("#stat-total").textContent = rows.length;
    $("#stat-checked").textContent = checked;
    $("#stat-risk").textContent = risky;
    $("#stat-failed").textContent = failed;
  }

  function renderNameCell(id, row) {
    if (state.editingId !== id) {
      return `<span class="node-name">${escapeHtml(row.name)}</span>`;
    }
    return `
      <div class="inline-edit">
        <input data-edit-input="${id}" value="${escapeHtml(row.name)}" aria-label="节点新名称" maxlength="200">
        <button type="button" data-action="save" data-id="${id}">保存</button>
        <button class="cancel-edit" type="button" data-action="cancel-edit" data-id="${id}">取消</button>
      </div>`;
  }

  function sortNumber(value) {
    const matches = String(value ?? "").match(/\d+(?:\.\d+)?/g);
    return matches ? Number(matches[matches.length - 1]) : null;
  }

  function compareRows(left, right) {
    const key = state.sort.key;
    let result;
    if (key === "risk" || key === "shared") {
      const leftNumber = sortNumber(
        key === "shared" ? sharedOrBot(left) : left[key],
      );
      const rightNumber = sortNumber(
        key === "shared" ? sharedOrBot(right) : right[key],
      );
      if (leftNumber !== null && rightNumber !== null) {
        result = leftNumber - rightNumber;
      } else if (leftNumber !== null || rightNumber !== null) {
        result = leftNumber !== null ? -1 : 1;
      }
    }
    if (result === undefined) {
      const leftValue = key === "status"
        ? rowStatusLabels[left.status] || left.status
        : left[key];
      const rightValue = key === "status"
        ? rowStatusLabels[right.status] || right.status
        : right[key];
      result = String(leftValue ?? "").localeCompare(
        String(rightValue ?? ""),
        "zh-CN",
        { numeric: true, sensitivity: "base" },
      );
    }
    if (result === 0) return Number(left.id) - Number(right.id);
    return state.sort.direction === "asc" ? result : -result;
  }

  function syncSortControls() {
    document.querySelectorAll("[data-sort-header]").forEach((header) => {
      const active = header.dataset.sortHeader === state.sort.key;
      header.setAttribute(
        "aria-sort",
        active
          ? state.sort.direction === "asc" ? "ascending" : "descending"
          : "none",
      );
      const button = $("[data-sort]", header);
      const arrow = $(".sort-arrow", button);
      const direction = state.sort.direction === "asc" ? "升序" : "降序";
      arrow.textContent = active
        ? state.sort.direction === "asc" ? "↑" : "↓"
        : "↕";
      button.setAttribute(
        "aria-label",
        active
          ? `${button.dataset.sortLabel}当前${direction}，点击切换`
          : `按${button.dataset.sortLabel}升序排序`,
      );
    });
  }

  function visibleRows() {
    const name = state.filters.name.toLocaleLowerCase();
    return [...state.rows.values()]
      .filter((row) => (
        (!name || String(row.name || "").toLocaleLowerCase().includes(name))
        && (!state.filters.type || row.type === state.filters.type)
        && (!state.filters.native || row.native === state.filters.native)
        && (!state.filters.source || row.source === state.filters.source)
        && (!state.filters.status || row.status === state.filters.status)
      ))
      .sort(compareRows);
  }

  function syncFilterOptions() {
    [
      [elements.filterType, "type"],
      [elements.filterNative, "native"],
      [elements.filterSource, "source"],
    ].forEach(([element, key]) => {
      const values = [...new Set(
        [...state.rows.values()]
          .map((row) => String(row[key] || ""))
          .filter(Boolean),
      )].sort((left, right) => left.localeCompare(right, "zh-CN"));
      element.replaceChildren(
        new Option(element.dataset.allLabel, ""),
        ...values.map((value) => new Option(value, value)),
      );
      if (!values.includes(state.filters[key])) state.filters[key] = "";
      element.value = state.filters[key];
    });
  }

  function resetFilters() {
    Object.keys(state.filters).forEach((key) => {
      state.filters[key] = "";
    });
    elements.filterName.value = "";
    elements.filterStatus.value = "";
    syncFilterOptions();
  }

  function shortError(value) {
    const error = String(value || "").replace(
      /^All sources failed\. Last:\s*/i,
      "",
    );
    if (/ssl|tls|certificate/i.test(error)) return "TLS 连接失败";
    if (/timed?\s*out|timeout/i.test(error)) return "检测超时";
    if (/cloudflare|\b403\b/i.test(error)) return "检测站拒绝访问";
    return error.length > 36 ? `${error.slice(0, 35)}…` : error;
  }

  function renderRows() {
    const rows = visibleRows();
    const hasResults = state.rows.size > 0;
    const editable = isTerminal() && !state.historyMode;
    elements.rows.innerHTML = rows.map((row) => {
      const id = Number(row.id);
      const busy = state.busyIds.has(id);
      const source = row.degraded
        ? `<span class="degraded" title="使用了回退数据源">${escapeHtml(row.source)} ↘</span>`
        : escapeHtml(row.source || "—");
      const error = row.status === "failed" ? String(row.error || "") : "";
      const status = `
        <div class="status-cell">
          <span class="status-mark status-${escapeHtml(row.status)}">${escapeHtml(rowStatusLabels[row.status] || row.status)}</span>
          ${error ? `<span class="row-error" title="${escapeHtml(error)}">${escapeHtml(shortError(error))}</span>` : ""}
        </div>`;
      return `
        <tr>
          <td class="select-cell">
            <input type="checkbox" data-select="${id}" aria-label="选择 ${escapeHtml(row.name)}" ${state.selected.has(id) ? "checked" : ""} ${state.historyMode ? "disabled" : ""}>
          </td>
          <td>${renderNameCell(id, row)}</td>
          <td class="mono">${escapeHtml(row.ip || "—")}</td>
          <td><span class="risk-mark ${riskLevel(row.risk)}">${escapeHtml(row.risk || "—")}</span></td>
          <td>${escapeHtml(sharedOrBot(row))}</td>
          <td>${escapeHtml(row.type || "—")}</td>
          <td>${escapeHtml(row.native || "—")}</td>
          <td>${source}</td>
          <td>${status}</td>
          <td class="actions-cell">
            <div class="row-actions">
              <button type="button" data-action="edit" data-id="${id}" ${editable && !busy ? "" : "disabled"}>改名</button>
              <button type="button" data-action="recheck" data-id="${id}" ${editable && !busy ? "" : "disabled"}>${busy ? "检测中" : "重检"}</button>
              <button class="delete-action" type="button" data-action="delete" data-id="${id}" ${editable && !busy ? "" : "disabled"}>删除</button>
            </div>
          </td>
        </tr>`;
    }).join("");
    if (hasResults && !rows.length) {
      elements.rows.innerHTML = `
        <tr class="no-filter-results">
          <td colspan="10">没有符合当前筛选条件的节点。</td>
        </tr>`;
    }

    elements.emptyState.hidden = hasResults;
    elements.tableWrap.hidden = !hasResults;
    const visibleSelected = rows.filter((row) => (
      state.selected.has(Number(row.id))
    )).length;
    const allSelected = rows.length > 0 && visibleSelected === rows.length;
    elements.selectAll.checked = allSelected;
    elements.selectAll.indeterminate = visibleSelected > 0 && !allSelected;
    elements.selectAll.disabled = state.historyMode || !rows.length;
    elements.selectedCount.textContent = state.historyMode
      ? "历史快照仅供查看"
      : state.selected.size
      ? `已选 ${state.selected.size} 个节点`
      : "未选择时导出全部";
    ["copy-yaml", "export-yaml", "export-csv"].forEach((id) => {
      $(`#${id}`).disabled = state.historyMode || !hasResults;
    });
    $("#open-clash").disabled = (
      state.historyMode || !hasResults || Boolean(state.apiToken)
    );
    $("#open-clash").title = state.apiToken
      ? "启用 API 令牌时请下载 YAML 后导入"
      : "";
    updateStats();
    syncSortControls();

    if (state.editingId !== null) {
      const input = $(`[data-edit-input="${state.editingId}"]`);
      input?.focus();
      input?.select();
    }
  }

  function applySnapshot(snapshot, historical = false) {
    if (!snapshot || typeof snapshot !== "object") return;
    const wasTerminal = terminalStatuses.has(state.status);
    if (snapshot.label) elements.jobLabel.textContent = snapshot.label;
    setStatus(snapshot.status || "unknown", snapshot.message);

    const hasResults = Array.isArray(snapshot.results);
    const incoming = hasResults ? snapshot.results : [];
    if (hasResults) {
      const incomingIds = new Set();
      incoming.forEach((row) => {
        if (row.id === undefined || row.id === null) return;
        const id = Number(row.id);
        incomingIds.add(id);
        state.rows.set(id, { ...row, id });
      });
      [...state.rows.keys()].forEach((id) => {
        if (!incomingIds.has(id)) {
          state.rows.delete(id);
          state.selected.delete(id);
        }
      });
    }

    const current = Number(snapshot.current) || 0;
    const total = Number(snapshot.total) || state.rows.size;
    elements.progressCount.textContent = `${current} / ${total}`;
    elements.progressBar.style.width = `${total ? Math.min(100, (current / total) * 100) : 0}%`;
    if (snapshot.error) showFormError(snapshot.error);
    setSubmitDisabled(!terminalStatuses.has(state.status));
    syncFilterOptions();
    renderRows();
    if (
      !historical
      && !wasTerminal
      && terminalStatuses.has(state.status)
    ) {
      setTimeout(loadHistory, 200);
    }
  }

  async function refreshSnapshot() {
    if (!state.jobId) return null;
    const snapshot = await api(`/api/jobs/${state.jobId}`);
    applySnapshot(snapshot);
    return snapshot;
  }

  function closeEvents() {
    clearTimeout(state.reconnectTimer);
    clearTimeout(state.pollTimer);
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  function setHistoryMode(enabled) {
    state.historyMode = enabled;
    if (!enabled) state.historyViewedId = null;
    elements.historyReadonly.hidden = !enabled;
    elements.returnCurrent.hidden = !enabled || !state.jobId;
    $('[data-view="raw"]').hidden = enabled;
    if (enabled) {
      state.selected.clear();
      state.editingId = null;
    }
  }

  function renderHistory(records) {
    elements.historyList.innerHTML = records.map((record) => {
      const finished = new Date(Number(record.finish_time) * 1000);
      const date = Number.isNaN(finished.getTime())
        ? "时间未知"
        : finished.toLocaleString();
      return `
        <li class="history-item">
          <div class="history-copy">
            <strong>${escapeHtml(record.label || "任务")}</strong>
            <span>${escapeHtml(date)} · ${escapeHtml(statusLabels[record.status] || record.status)}</span>
          </div>
          <span class="history-counts">${escapeHtml(record.checked || 0)} / ${escapeHtml(record.total || 0)} 节点 · ${escapeHtml(record.failed || 0)} 异常 · ${escapeHtml(record.skipped || 0)} 跳过</span>
          <div class="history-actions">
            <button class="secondary" type="button" data-history-view="${escapeHtml(record.job_id)}">查看</button>
            <button class="delete-action" type="button" data-history-delete="${escapeHtml(record.job_id)}">删除</button>
          </div>
        </li>`;
    }).join("");
  }

  async function loadHistory() {
    const enteredToken = $("#api-token").value.trim();
    if (enteredToken) state.apiToken = enteredToken;
    elements.refreshHistory.disabled = true;
    elements.historyStatus.textContent = "正在加载…";
    elements.historyList.replaceChildren();
    try {
      const data = await api("/api/history");
      const records = Array.isArray(data.records) ? data.records : [];
      renderHistory(records);
      elements.historyStatus.textContent = records.length
        ? `保留最近 ${records.length} 条终态记录`
        : "暂无历史记录";
    } catch (error) {
      elements.historyStatus.textContent = `历史记录加载失败：${error.message}`;
    } finally {
      elements.refreshHistory.disabled = false;
    }
  }

  async function viewHistory(jobId) {
    try {
      const snapshot = await api(`/api/history/${jobId}`);
      closeEvents();
      state.historyViewedId = jobId;
      setHistoryMode(true);
      state.view = "table";
      document.querySelectorAll("[data-view]").forEach((button) => {
        const active = button.dataset.view === "table";
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
      });
      elements.tableView.hidden = false;
      elements.rawPanel.hidden = true;
      elements.workspace.hidden = false;
      applySnapshot(snapshot, true);
      elements.workspace.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      showToast(error.message);
    }
  }

  async function returnCurrentJob() {
    setHistoryMode(false);
    if (!state.jobId) {
      elements.workspace.hidden = true;
      return;
    }
    resetWorkspace();
    try {
      const snapshot = await refreshSnapshot();
      if (!terminalStatuses.has(snapshot.status)) connectEvents();
    } catch (error) {
      rememberJob(null);
      state.jobId = null;
      elements.workspace.hidden = true;
      showToast(error.message);
    }
  }

  async function deleteHistory(jobId) {
    if (!window.confirm("删除这条历史记录？")) return;
    try {
      await api(`/api/history/${jobId}`, { method: "DELETE" });
      if (state.historyMode && state.historyViewedId === jobId) {
        await returnCurrentJob();
      }
      await loadHistory();
      showToast("历史记录已删除");
    } catch (error) {
      showToast(error.message);
    }
  }

  function scheduleReconnect() {
    closeEvents();
    if (terminalStatuses.has(state.status)) return;
    if (state.reconnects >= 3) {
      setStatus("unknown", "实时连接中断，请刷新页面后重试");
      setSubmitDisabled(false);
      return;
    }
    state.reconnects += 1;
    setStatus("reconnecting", `正在进行第 ${state.reconnects} 次重连`);
    const delay = 800 * (2 ** (state.reconnects - 1));
    state.reconnectTimer = setTimeout(async () => {
      try {
        const snapshot = await refreshSnapshot();
        if (snapshot && !terminalStatuses.has(snapshot.status)) connectEvents();
      } catch {
        scheduleReconnect();
      }
    }, delay);
  }

  function connectEvents() {
    closeEvents();
    if (!state.jobId || terminalStatuses.has(state.status)) return;
    if (state.apiToken) {
      state.pollTimer = setTimeout(pollProtectedJob, 700);
      return;
    }
    const source = new EventSource(`/api/jobs/${state.jobId}/events`);
    state.eventSource = source;
    source.onopen = () => {
      state.reconnects = 0;
    };
    source.onmessage = (event) => {
      try {
        const snapshot = JSON.parse(event.data);
        applySnapshot(snapshot);
        if (terminalStatuses.has(snapshot.status)) closeEvents();
      } catch {
        scheduleReconnect();
      }
    };
    source.onerror = scheduleReconnect;
  }

  async function pollProtectedJob() {
    try {
      const snapshot = await refreshSnapshot();
      state.reconnects = 0;
      if (!terminalStatuses.has(snapshot.status)) {
        state.pollTimer = setTimeout(pollProtectedJob, 700);
      }
    } catch {
      scheduleReconnect();
    }
  }

  function resetWorkspace() {
    closeEvents();
    setHistoryMode(false);
    state.rows.clear();
    state.selected.clear();
    state.editingId = null;
    state.raw = "";
    state.view = "table";
    state.sort = { key: "id", direction: "asc" };
    resetFilters();
    elements.rawView.textContent = "正在读取…";
    elements.tableView.hidden = false;
    elements.rawPanel.hidden = true;
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === "table";
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    elements.workspace.hidden = false;
    elements.progressCount.textContent = "0 / 0";
    elements.progressMessage.textContent = "正在提交";
    elements.progressBar.style.width = "0";
    renderRows();
  }

  async function startJob(event) {
    event.preventDefault();
    showFormError();
    state.apiToken = $("#api-token").value.trim();
    const kind = $('input[name="kind"]:checked').value;
    const skipKeywords = $("#skip").value.trim();
    const options = {
      mode: $("#mode").value,
      source: $("#source").value,
      fallback: $("#fallback").checked,
      request_timeout: Number($("#timeout").value),
      max_age: Number($("#max-age").value),
      headless: true,
    };
    if (skipKeywords) options.skip_keywords = skipKeywords;
    const payload = { options };
    if (kind === "url") {
      const input = $("#url");
      if (!input.value || !input.validity.valid) {
        showFormError("请输入有效的 HTTP(S) 订阅地址。");
        input.focus();
        return;
      }
      payload.url = input.value.trim();
    } else {
      const yaml = $("#yaml").value.trim();
      if (!yaml) {
        showFormError("请粘贴 YAML，或先从文件读取。");
        $("#yaml").focus();
        return;
      }
      payload.yaml = yaml;
    }

    resetWorkspace();
    setSubmitDisabled(true);
    setStatus("queued", "正在创建任务");
    try {
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.jobId = job.job_id;
      state.status = job.status;
      rememberJob(state.jobId);
      elements.jobLabel.textContent = job.label || "当前任务";
      $("#url").value = "";
      $("#yaml").value = "";
      $("#yaml-file").value = "";
      $("#api-token").value = "";
      $("#file-name").textContent = "支持 .yaml / .yml，最大 5 MB";
      await refreshSnapshot();
      connectEvents();
      elements.workspace.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      state.status = "error";
      setSubmitDisabled(false);
      showFormError(error.message);
      setStatus("error", "任务创建失败");
    }
  }

  async function cancelJob() {
    if (!state.jobId) return;
    elements.cancel.disabled = true;
    try {
      await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
      await refreshSnapshot();
    } catch (error) {
      showToast(error.message);
    }
  }

  async function runNodeAction(action, id) {
    if (state.historyMode || !state.jobId || state.busyIds.has(id)) return;
    const row = state.rows.get(id);
    if (!row) return;

    if (action === "edit") {
      state.editingId = id;
      renderRows();
      return;
    }
    if (action === "cancel-edit") {
      state.editingId = null;
      renderRows();
      return;
    }
    if (action === "save") {
      const input = $(`[data-edit-input="${id}"]`);
      const name = input?.value.trim();
      if (!name) {
        input?.focus();
        return;
      }
      state.busyIds.add(id);
      try {
        await api(`/api/jobs/${state.jobId}/nodes/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        state.editingId = null;
        state.raw = "";
        await refreshSnapshot();
        showToast("节点名称已保存");
      } catch (error) {
        showToast(error.message);
      } finally {
        state.busyIds.delete(id);
        renderRows();
      }
      return;
    }
    if (action === "delete" && !window.confirm(`删除“${row.name}”？`)) return;

    state.busyIds.add(id);
    renderRows();
    try {
      const suffix = action === "recheck" ? "/recheck" : "";
      const method = action === "delete" ? "DELETE" : "POST";
      await api(`/api/jobs/${state.jobId}/nodes/${id}${suffix}`, { method });
      state.raw = "";
      await refreshSnapshot();
      showToast(action === "delete" ? "节点已删除" : "节点已重新检测");
    } catch (error) {
      showToast(error.message);
    } finally {
      state.busyIds.delete(id);
      renderRows();
    }
  }

  async function fetchRaw(force = false) {
    if (state.historyMode || !state.jobId) return "";
    if (!state.raw || force) {
      state.raw = await api(`/api/jobs/${state.jobId}/raw`);
    }
    elements.rawView.textContent = state.raw;
    return state.raw;
  }

  async function switchView(view) {
    if (state.historyMode && view === "raw") return;
    state.view = view;
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    elements.tableView.hidden = view !== "table";
    elements.rawPanel.hidden = view !== "raw";
    if (view === "raw") {
      try {
        await fetchRaw(true);
      } catch (error) {
        elements.rawView.textContent = error.message;
      }
    }
  }

  async function exportData() {
    if (state.historyMode) throw new Error("历史快照仅供查看");
    const nodeIds = [...state.selected];
    return api(`/api/jobs/${state.jobId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ node_ids: nodeIds }),
    });
  }

  function download(text, filename, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  async function handleExport(kind) {
    try {
      const data = await exportData();
      if (kind === "copy") {
        await copyText(data.yaml);
        showToast("YAML 已复制");
      } else if (kind === "yaml") {
        download(data.yaml, data.yaml_filename, "application/yaml;charset=utf-8");
      } else {
        download(data.csv, data.csv_filename, "text/csv;charset=utf-8");
      }
    } catch (error) {
      showToast(error.message);
    }
  }

  function openInClash() {
    if (state.historyMode || !state.jobId) return;
    if (state.apiToken) {
      showToast("启用 API 令牌时，请下载 YAML 后在 Clash 中导入");
      return;
    }
    const rawUrl = new URL(`/api/jobs/${state.jobId}/raw`, window.location.href);
    window.location.href = `clash://install-config?url=${encodeURIComponent(rawUrl.href)}&name=${encodeURIComponent("Clash IP Checker")}`;
  }

  document.querySelectorAll('input[name="kind"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const yamlMode = radio.value === "yaml" && radio.checked;
      $("#url-pane").hidden = yamlMode;
      $("#yaml-pane").hidden = !yamlMode;
    });
  });

  $("#mode").addEventListener("change", (event) => {
    const browserMode = event.target.value === "browser";
    $("#source").disabled = browserMode;
  });

  $("#yaml-file").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      showFormError("YAML 文件不能超过 5 MB。");
      event.target.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      $("#yaml").value = String(reader.result || "");
      $("#file-name").textContent = file.name;
      showFormError();
    };
    reader.onerror = () => showFormError("无法读取这个文件。");
    reader.readAsText(file);
  });

  elements.form.addEventListener("submit", startJob);
  elements.cancel.addEventListener("click", cancelJob);
  elements.selectAll.addEventListener("change", () => {
    visibleRows().forEach((row) => {
      const id = Number(row.id);
      if (elements.selectAll.checked) state.selected.add(id);
      else state.selected.delete(id);
    });
    renderRows();
  });
  elements.filterName.addEventListener("input", (event) => {
    state.filters.name = event.target.value.trim();
    renderRows();
  });
  document.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      if (state.sort.key === key) {
        state.sort.direction = state.sort.direction === "asc" ? "desc" : "asc";
      } else {
        state.sort = { key, direction: "asc" };
      }
      renderRows();
    });
  });
  [
    [elements.filterType, "type"],
    [elements.filterNative, "native"],
    [elements.filterSource, "source"],
    [elements.filterStatus, "status"],
  ].forEach(([element, key]) => {
    element.addEventListener("change", () => {
      state.filters[key] = element.value;
      renderRows();
    });
  });
  elements.rows.addEventListener("change", (event) => {
    const id = event.target.dataset.select;
    if (id === undefined) return;
    if (event.target.checked) {
      state.selected.add(Number(id));
    } else {
      state.selected.delete(Number(id));
    }
    renderRows();
  });
  elements.rows.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    runNodeAction(button.dataset.action, Number(button.dataset.id));
  });
  elements.rows.addEventListener("keydown", (event) => {
    if (!event.target.matches("[data-edit-input]")) return;
    const id = Number(event.target.dataset.editInput);
    if (event.key === "Enter") runNodeAction("save", id);
    if (event.key === "Escape") runNodeAction("cancel-edit", id);
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $("#refresh-raw").addEventListener("click", () => fetchRaw(true));
  $("#copy-yaml").addEventListener("click", () => handleExport("copy"));
  $("#export-yaml").addEventListener("click", () => handleExport("yaml"));
  $("#export-csv").addEventListener("click", () => handleExport("csv"));
  $("#open-clash").addEventListener("click", openInClash);
  elements.refreshHistory.addEventListener("click", loadHistory);
  elements.returnCurrent.addEventListener("click", returnCurrentJob);
  elements.historyList.addEventListener("click", (event) => {
    const viewButton = event.target.closest("[data-history-view]");
    if (viewButton) {
      viewHistory(viewButton.dataset.historyView);
      return;
    }
    const deleteButton = event.target.closest("[data-history-delete]");
    if (deleteButton) deleteHistory(deleteButton.dataset.historyDelete);
  });

  async function restoreJob() {
    const jobId = recalledJob();
    if (!jobId) return;
    state.jobId = jobId;
    elements.workspace.hidden = false;
    try {
      const snapshot = await refreshSnapshot();
      if (!terminalStatuses.has(snapshot.status)) connectEvents();
    } catch {
      rememberJob(null);
      state.jobId = null;
      setStatus("unknown", "上次任务已过期，可以创建新任务");
      setSubmitDisabled(false);
    }
  }

  loadHistory();
  restoreJob();
})();
