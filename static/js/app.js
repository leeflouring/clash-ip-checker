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
    editDraft: "",
    navigation: 0,
    streamGeneration: 0,
    historyRequest: 0,
    viewRequest: 0,
    rawRequest: 0,
    submitting: false,
    layout: "compose",
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
    resultsContent: $("#results-content"),
    emptyState: $("#empty-state"),
    emptyStateIcon: $("#empty-state-icon"),
    emptyStateTitle: $("#empty-state-title"),
    emptyStateCopy: $("#empty-state-copy"),
    emptyStateAction: $("#empty-state-action"),
    selectAll: $("#select-all"),
    mobileSelectAll: $("#mobile-select-all"),
    mobileResults: $("#mobile-results"),
    filterName: $("#filter-name"),
    filterType: $("#filter-type"),
    filterNative: $("#filter-native"),
    filterSource: $("#filter-source"),
    filterStatus: $("#filter-status"),
    mobileFilterName: $("#mobile-filter-name"),
    mobileFilterType: $("#mobile-filter-type"),
    mobileFilterNative: $("#mobile-filter-native"),
    mobileFilterSource: $("#mobile-filter-source"),
    mobileFilterStatus: $("#mobile-filter-status"),
    mobileSort: $("#mobile-sort"),
    mobileSortDirection: $("#mobile-sort-direction"),
    selectedCount: $("#selected-count"),
    visibleCount: $("#visible-count"),
    selectionNote: $("#selection-note"),
    selectionTools: $("#selection-tools"),
    clearFilters: $("#clear-filters"),
    exportMenu: $("#export-menu"),
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
    sidebar: $("#sidebar"),
    sidebarToggle: $("#sidebar-toggle"),
    sidebarBackdrop: $("#sidebar-backdrop"),
    historyCountBadge: $("#history-count-badge"),
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
      const error = new Error(message || `请求失败（${response.status}）`);
      error.status = response.status;
      throw error;
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
    elements.state.dataset.status = status;
    if (message) {
      elements.progressMessage.textContent = message;
    }
    renderWorkspaceState();
    updateNavigation();
  }

  function isTerminal() {
    return terminalStatuses.has(state.status);
  }

  function setSubmitDisabled(disabled) {
    elements.form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = disabled;
    });
    elements.cancel.disabled = !disabled;
    elements.cancel.hidden = !disabled;
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
    return "暂无";
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

  function filtersActive() {
    return Object.values(state.filters).some(Boolean);
  }

  function renderWorkspaceState() {
    const hasResults = state.rows.size > 0;
    let workspaceState = state.status;
    let icon = "⌁";
    let title = "结果将在这里出现";
    let copy = "从上方提交订阅或 YAML，检测进度会实时更新。";
    let action = "填写配置";

    if (hasResults) {
      workspaceState = state.historyMode ? "history-readonly" : "results";
    } else if (["queued", "running", "reconnecting"].includes(state.status)) {
      workspaceState = "loading";
      icon = "…";
      title = state.status === "reconnecting" ? "正在恢复实时连接" : "正在准备节点结果";
      copy = state.status === "reconnecting"
        ? "已完成的结果会保留，连接恢复后继续更新。"
        : "配置解析和检测开始后，节点会逐项出现。";
      action = "";
    } else if (state.status === "unknown") {
      workspaceState = "connection-error";
      icon = "×";
      title = "无法恢复当前任务";
      copy = "实时连接已中断或任务已过期。可以刷新任务，或提交一份新配置。";
      action = state.jobId ? "重试读取" : "新建检测";
    } else if (state.status === "error") {
      workspaceState = "error";
      icon = "×";
      title = "任务未能完成";
      copy = elements.progressMessage.textContent || "请检查配置和服务状态后重试。";
      action = "重新填写";
    } else if (terminalStatuses.has(state.status)) {
      workspaceState = "terminal-empty";
      icon = "○";
      title = state.status === "cancelled" ? "任务已取消" : "没有可显示的节点";
      copy = state.status === "cancelled"
        ? "已收到的结果会保留；本次任务尚未产生节点。"
        : "配置中没有可检查的节点，或所有节点已被删除。";
      action = "检查新配置";
    } else {
      workspaceState = "idle";
    }

    elements.workspace.dataset.workspaceState = workspaceState;
    elements.workspace.dataset.historyMode = String(state.historyMode);
    elements.emptyStateIcon.textContent = icon;
    elements.emptyStateTitle.textContent = title;
    elements.emptyStateCopy.textContent = copy;
    elements.emptyStateAction.textContent = action;
    elements.emptyStateAction.hidden = !action;
    elements.emptyState.hidden = hasResults;
    elements.resultsContent.hidden = !hasResults;
  }

  function scrollToWorkspace() {
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    elements.workspace.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "start",
    });
  }

  function findEditInput(id) {
    const inputs = [...document.querySelectorAll(`[data-edit-input="${id}"]`)];
    return inputs.find((input) => input.offsetParent !== null) || inputs[0] || null;
  }

  function renderNameCell(id, row) {
    if (state.editingId !== id) {
      return `<span class="node-name">${escapeHtml(row.name)}</span>`;
    }
    return `
      <div class="inline-edit">
        <input data-edit-input="${id}" value="${escapeHtml(state.editDraft)}" aria-label="节点新名称" maxlength="200">
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
    elements.mobileSort.value = state.sort.key;
    const ascending = state.sort.direction === "asc";
    elements.mobileSortDirection.textContent = ascending ? "↑" : "↓";
    elements.mobileSortDirection.setAttribute(
      "aria-label",
      ascending ? "当前升序，点击切换为降序" : "当前降序，点击切换为升序",
    );
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
      [elements.filterType, elements.mobileFilterType, "type"],
      [elements.filterNative, elements.mobileFilterNative, "native"],
      [elements.filterSource, elements.mobileFilterSource, "source"],
    ].forEach(([desktop, mobile, key]) => {
      const values = [...new Set(
        [...state.rows.values()]
          .map((row) => String(row[key] || ""))
          .filter(Boolean),
      )].sort((left, right) => left.localeCompare(right, "zh-CN"));
      if (!values.includes(state.filters[key])) state.filters[key] = "";
      [desktop, mobile].forEach((element) => {
        if (JSON.stringify([...element.options].slice(1).map((option) => option.value)) !== JSON.stringify(values)) {
          element.replaceChildren(
            new Option(element.dataset.allLabel, ""),
            ...values.map((value) => new Option(value, value)),
          );
        }
        element.value = state.filters[key];
      });
    });
    syncFilterControls();
  }

  function syncFilterControls() {
    elements.filterName.value = state.filters.name;
    elements.mobileFilterName.value = state.filters.name;
    elements.filterStatus.value = state.filters.status;
    elements.mobileFilterStatus.value = state.filters.status;
    [
      [elements.filterType, elements.mobileFilterType, "type"],
      [elements.filterNative, elements.mobileFilterNative, "native"],
      [elements.filterSource, elements.mobileFilterSource, "source"],
    ].forEach(([desktop, mobile, key]) => {
      desktop.value = state.filters[key];
      mobile.value = state.filters[key];
    });
  }

  function resetFilters() {
    Object.keys(state.filters).forEach((key) => {
      state.filters[key] = "";
    });
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

  function sourceMarkup(row) {
    return row.degraded
      ? `<span class="degraded" title="使用了回退数据源">${escapeHtml(row.source || "暂无")}（回退）</span>`
      : escapeHtml(row.source || "暂无");
  }

  function statusMarkup(row) {
    const error = row.status === "failed" ? String(row.error || "") : "";
    return `
      <div class="status-cell">
        <span class="status-mark status-${escapeHtml(row.status)}">${escapeHtml(rowStatusLabels[row.status] || row.status)}</span>
        ${error ? `<span class="row-error" title="${escapeHtml(error)}">${escapeHtml(shortError(error))}</span>` : ""}
      </div>`;
  }

  function rowActions(id, busy, editable) {
    const enabled = editable && !busy;
    return `
      <div class="row-actions">
        <button type="button" data-action="edit" data-id="${id}" ${enabled ? "" : "disabled"}>改名</button>
        <button type="button" data-action="recheck" data-id="${id}" ${enabled ? "" : "disabled"}>${busy ? "检测中" : "重检"}</button>
        <button class="delete-action" type="button" data-action="delete" data-id="${id}" ${enabled ? "" : "disabled"}>删除</button>
      </div>`;
  }

  function renderMobileRow(row, editable) {
    const id = Number(row.id);
    const busy = state.busyIds.has(id);
    const error = row.status === "failed" ? String(row.error || "") : "";
    return `
      <article class="mobile-result" data-node-id="${id}">
        <div class="mobile-result-head">
          <label class="mobile-select">
            <span class="sr-only">选择 ${escapeHtml(row.name)}</span>
            <input type="checkbox" data-select="${id}" ${state.selected.has(id) ? "checked" : ""} ${state.historyMode ? "disabled" : ""}>
          </label>
          <div class="mobile-result-name">${renderNameCell(id, row)}</div>
          <span class="status-mark status-${escapeHtml(row.status)}">${escapeHtml(rowStatusLabels[row.status] || row.status)}</span>
        </div>
        <div class="mobile-facts">
          <div class="mobile-fact"><span>出口 IP</span><strong class="mono">${escapeHtml(row.ip || "暂无")}</strong></div>
          <div class="mobile-fact"><span>风险</span><strong><span class="risk-mark ${riskLevel(row.risk)}">${escapeHtml(row.risk || "暂无")}</span></strong></div>
        </div>
        <details>
          <summary>更多信息</summary>
          <dl class="mobile-details">
            <div><dt>属性</dt><dd>${escapeHtml(row.type || "暂无")}</dd></div>
            <div><dt>原生性</dt><dd>${escapeHtml(row.native || "暂无")}</dd></div>
            <div><dt>数据源</dt><dd>${sourceMarkup(row)}</dd></div>
            <div><dt>共享 / Bot</dt><dd>${escapeHtml(sharedOrBot(row))}</dd></div>
            ${error ? `<div class="mobile-error"><dt>异常信息</dt><dd>${escapeHtml(error)}</dd></div>` : ""}
          </dl>
        </details>
        ${rowActions(id, busy, editable)}
      </article>`;
  }

  function renderSelectionSummary(rows) {
    const hasResults = state.rows.size > 0;
    const visibleSelected = rows.filter((row) => (
      state.selected.has(Number(row.id))
    )).length;
    const allSelected = rows.length > 0 && visibleSelected === rows.length;
    [elements.selectAll, elements.mobileSelectAll].forEach((input) => {
      input.checked = allSelected;
      input.indeterminate = visibleSelected > 0 && !allSelected;
      input.disabled = state.historyMode || !rows.length;
    });
    elements.visibleCount.textContent = `${rows.length} / ${state.rows.size} 个可见节点`;
    elements.clearFilters.disabled = !filtersActive();
    elements.selectionTools.hidden = state.historyMode;
    elements.exportMenu.hidden = state.historyMode;
    if (state.historyMode) {
      elements.selectedCount.textContent = "历史快照仅供查看";
    } else if (state.selected.size) {
      elements.selectedCount.textContent = `导出已选 ${state.selected.size} 个节点`;
    } else {
      elements.selectedCount.textContent = "未选择时导出全部节点";
    }
    const hiddenSelected = state.selected.size - visibleSelected;
    elements.selectionNote.textContent = hiddenSelected > 0
      ? `${hiddenSelected} 个隐藏选择仍会保留`
      : "隐藏的选择会保留";
    ["copy-yaml", "export-yaml", "export-csv"].forEach((id) => {
      $(`#${id}`).disabled = state.historyMode || !hasResults;
    });
    $("#open-clash").disabled = (
      state.historyMode || !hasResults || Boolean(state.apiToken)
    );
    $("#open-clash").title = state.apiToken
      ? "启用 API 令牌时请下载 YAML 后导入"
      : "";
  }

  // Keep unchanged row elements alive: selection, drafts and open mobile details
  // survive a snapshot, and one changed node replaces only its own two renderers.
  function reconcileRows(container, rows, editable, render) {
    const existing = new Map([...container.children].map((node) => [node.dataset.nodeId, node]));
    const keep = new Set();
    rows.forEach((row, index) => {
      const id = Number(row.id);
      const key = String(id);
      const signature = JSON.stringify([row, editable, state.historyMode,
        state.selected.has(id), state.editingId === id, state.busyIds.has(id)]);
      let node = existing.get(key);
      if (!node || node.dataset.signature !== signature) {
        const holder = document.createElement(container.tagName === "TBODY" ? "tbody" : "div");
        holder.innerHTML = render(row, editable);
        const replacement = holder.firstElementChild;
        replacement.dataset.signature = signature;
        const details = node?.querySelector("details");
        if (details?.open && replacement.querySelector("details")) replacement.querySelector("details").open = true;
        if (node) node.replaceWith(replacement);
        node = replacement;
      }
      keep.add(node);
      if (container.children[index] !== node) container.insertBefore(node, container.children[index] || null);
    });
    [...container.children].forEach((node) => { if (!keep.has(node)) node.remove(); });
    if (!rows.length && state.rows.size && !container.querySelector(".no-filter-results, .mobile-empty")) {
      container.innerHTML = container.tagName === "TBODY"
        ? '<tr class="no-filter-results"><td colspan="10">没有符合当前筛选条件的节点。请调整或清除筛选。</td></tr>'
        : '<div class="mobile-empty" role="status">没有符合当前筛选条件的节点。请调整或清除筛选。</div>';
    }
  }

  function renderDesktopRow(row, editable) {
    const id = Number(row.id);
    const busy = state.busyIds.has(id);
      return `
        <tr data-node-id="${id}">
          <td class="select-cell"><input type="checkbox" data-select="${id}" aria-label="选择 ${escapeHtml(row.name)}" ${state.selected.has(id) ? "checked" : ""} ${state.historyMode ? "disabled" : ""}></td>
          <td>${renderNameCell(id, row)}</td>
          <td class="mono">${escapeHtml(row.ip || "暂无")}</td>
          <td><span class="risk-mark ${riskLevel(row.risk)}">${escapeHtml(row.risk || "暂无")}</span></td>
          <td>${escapeHtml(sharedOrBot(row))}</td>
          <td>${escapeHtml(row.type || "暂无")}</td>
          <td>${escapeHtml(row.native || "暂无")}</td>
          <td>${sourceMarkup(row)}</td>
          <td>${statusMarkup(row)}</td>
          <td class="actions-cell">${rowActions(id, busy, editable)}</td>
        </tr>`;

  }

  function renderRows() {
    const rows = visibleRows();
    const editable = isTerminal() && !state.historyMode;
    const focused = document.activeElement;
    const focusedRow = focused?.closest("[data-node-id]");
    const focusKey = focused?.dataset.editInput !== undefined ? "editInput"
      : focused?.dataset.select !== undefined ? "select" : null;
    const selection = focusKey === "editInput" ? [focused.selectionStart, focused.selectionEnd] : null;
    reconcileRows(elements.rows, rows, editable, renderDesktopRow);
    reconcileRows(elements.mobileResults, rows, editable, renderMobileRow);
    renderWorkspaceState();
    renderSelectionSummary(rows);
    updateStats();
    syncFilterControls();
    syncSortControls();
    if (focusedRow && focusKey && !focused.isConnected) {
      const replacement = [...document.querySelectorAll(focusKey === "editInput" ? "[data-edit-input]" : "[data-select]")]
        .find((input) => input.dataset[focusKey] === focused.dataset[focusKey] && input.offsetParent !== null);
      replacement?.focus();
      if (selection) replacement?.setSelectionRange(...selection);
    }
  }

  function applySnapshot(snapshot, historical = false) {
    if (!snapshot || typeof snapshot !== "object") return;
    const wasTerminal = terminalStatuses.has(state.status);
    let rowsChanged = false;
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
        const normalized = { ...row, id };
        if (JSON.stringify(state.rows.get(id)) !== JSON.stringify(normalized)) {
          state.rows.set(id, normalized);
          rowsChanged = true;
        }
      });
      [...state.rows.keys()].forEach((id) => {
        if (!incomingIds.has(id)) {
          state.rows.delete(id);
          rowsChanged = true;
          state.selected.delete(id);
        }
      });
    }

    const current = Number(snapshot.current) || 0;
    const total = Number(snapshot.total) || state.rows.size;
    const percent = total ? Math.min(100, (current / total) * 100) : 0;
    elements.progressCount.textContent = `${current} / ${total}`;
    elements.progressBar.style.setProperty("--progress", String(percent / 100));
    $(".progress-track").setAttribute("aria-valuenow", String(Math.round(percent)));
    if (snapshot.error) showFormError(snapshot.error);
    setSubmitDisabled(!terminalStatuses.has(state.status));
    if (rowsChanged) syncFilterOptions();
    if (rowsChanged || wasTerminal !== isTerminal()) renderRows();
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
    const jobId = state.jobId;
    const navigation = state.navigation;
    const snapshot = await api(`/api/jobs/${jobId}`);
    if (navigation !== state.navigation || jobId !== state.jobId || state.historyMode) return null;
    applySnapshot(snapshot);
    return snapshot;
  }

  function closeEvents() {
    state.streamGeneration += 1;
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
    updateNavigation();
    $('[data-view="raw"]').hidden = enabled;
    elements.exportMenu.hidden = enabled;
    elements.exportMenu.open = false;
    elements.selectionTools.hidden = enabled;
    elements.workspace.dataset.historyMode = String(enabled);
    if (enabled) {
      state.selected.clear();
      state.editingId = null;
    }
    renderWorkspaceState();
  }

  function renderHistory(records) {
    elements.historyCountBadge.textContent = String(records.length);
    elements.historyCountBadge.setAttribute(
      "aria-label",
      `历史记录数量：${records.length}`,
    );
    elements.historyList.innerHTML = records.map((record) => {
      const finished = new Date(Number(record.finish_time) * 1000);
      const date = Number.isNaN(finished.getTime())
        ? "时间未知"
        : finished.toLocaleString();
      return `
        <li class="history-item ${state.historyViewedId === record.job_id ? "is-active" : ""}">
          <button class="history-open" type="button" data-history-view="${escapeHtml(record.job_id)}" aria-label="查看 ${escapeHtml(record.label || "任务")}">
            <span class="history-copy"><strong>${escapeHtml(record.label || "任务")}</strong><span>${escapeHtml(date)}<br>${escapeHtml(statusLabels[record.status] || record.status)} · ${escapeHtml(record.total || 0)} 个节点</span></span>
          </button>
          <button class="history-delete" type="button" data-history-delete="${escapeHtml(record.job_id)}" aria-label="删除 ${escapeHtml(record.label || "任务")} 历史记录" title="删除历史记录">×</button>
        </li>`;
    }).join("");
  }

  async function loadHistory() {
    const request = ++state.historyRequest;
    const enteredToken = $("#api-token").value.trim();
    if (enteredToken) state.apiToken = enteredToken;
    elements.refreshHistory.disabled = true;
    elements.historyStatus.textContent = "正在加载…";
    elements.historyList.innerHTML = `
      <li class="history-skeleton" aria-hidden="true"><span></span><span></span></li>
      <li class="history-skeleton" aria-hidden="true"><span></span><span></span></li>`;
    try {
      const data = await api("/api/history");
      if (request !== state.historyRequest) return;
      const records = Array.isArray(data.records) ? data.records : [];
      renderHistory(records);
      elements.historyStatus.textContent = records.length
        ? `保留最近 ${records.length} 条终态记录`
        : "暂无历史记录";
      if (!records.length) elements.historyList.replaceChildren();
    } catch (error) {
      if (request !== state.historyRequest) return;
      elements.historyCountBadge.textContent = "0";
      elements.historyCountBadge.setAttribute("aria-label", "历史记录数量：0");
      elements.historyStatus.textContent = `历史记录加载失败：${error.message}`;
      elements.historyList.replaceChildren();
    } finally {
      if (request === state.historyRequest) elements.refreshHistory.disabled = false;
    }
  }

  async function viewHistory(jobId) {
    if (state.submitting || state.busyIds.size) return;
    const request = ++state.viewRequest;
    const navigation = state.navigation;
    try {
      const snapshot = await api(`/api/history/${jobId}`);
      if (request !== state.viewRequest || navigation !== state.navigation) return;
      state.navigation += 1;
      closeEvents();
      state.historyViewedId = jobId;
      setHistoryMode(true);
      await activateViewTab("table", false);
      applySnapshot(snapshot, true);
      renderRows();
      setLayout("results");
      setSidebar(false);
      document.querySelectorAll(".history-item").forEach((item) => item.classList.toggle("is-active", item.querySelector("[data-history-view]").dataset.historyView === jobId));
      scrollToWorkspace();
    } catch (error) {
      showToast(error.message);
    }
  }

  async function returnCurrentJob() {
    if (state.submitting || state.busyIds.size) return;
    state.viewRequest += 1;
    const navigation = ++state.navigation;
    const enteredToken = $("#api-token").value.trim();
    if (enteredToken) state.apiToken = enteredToken;
    setHistoryMode(false);
    setSidebar(false);
    setLayout(state.jobId ? "results" : "compose");
    if (!state.jobId) {
      state.rows.clear();
      state.status = "idle";
      elements.jobLabel.textContent = "尚未创建任务";
      elements.progressCount.textContent = "0 / 0";
      elements.progressMessage.textContent = "等待提交配置";
      renderRows();
      return;
    }
    resetWorkspace();
    try {
      const snapshot = await refreshSnapshot();
      if (snapshot && !terminalStatuses.has(snapshot.status)) connectEvents();
    } catch (error) {
      if (navigation !== state.navigation) return;
      if (error.status === 404) {
        rememberJob(null);
        state.jobId = null;
      }
      setStatus("unknown", error.status === 404 ? "当前任务已过期，可以创建新任务" : "无法恢复当前任务，请检查令牌或连接后重试");
      renderRows();
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
    const generation = state.streamGeneration;
    state.reconnectTimer = setTimeout(async () => {
      try {
        const snapshot = await refreshSnapshot();
        if (snapshot && generation === state.streamGeneration && !terminalStatuses.has(snapshot.status)) connectEvents();
      } catch {
        if (generation === state.streamGeneration) scheduleReconnect();
      }
    }, delay);
  }

  function connectEvents() {
    closeEvents();
    if (!state.jobId || state.historyMode || terminalStatuses.has(state.status)) return;
    if (state.apiToken) {
      state.pollTimer = setTimeout(pollProtectedJob, 700);
      return;
    }
    const source = new EventSource(`/api/jobs/${state.jobId}/events`);
    state.eventSource = source;
    source.onmessage = (event) => {
      if (state.eventSource !== source || state.historyMode) return;
      try {
        const snapshot = JSON.parse(event.data);
        applySnapshot(snapshot);
        state.reconnects = 0;
        if (terminalStatuses.has(snapshot.status)) closeEvents();
      } catch {
        scheduleReconnect();
      }
    };
    source.onerror = () => { if (state.eventSource === source) scheduleReconnect(); };
  }

  async function pollProtectedJob() {
    const generation = state.streamGeneration;
    try {
      const snapshot = await refreshSnapshot();
      if (!snapshot || generation !== state.streamGeneration) return;
      state.reconnects = 0;
      if (snapshot && !terminalStatuses.has(snapshot.status)) {
        state.pollTimer = setTimeout(pollProtectedJob, 700);
      }
    } catch {
      if (generation === state.streamGeneration) scheduleReconnect();
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
    elements.rawView.classList.remove("is-error");
    elements.tableView.hidden = false;
    elements.rawPanel.hidden = true;
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === "table";
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    elements.progressCount.textContent = "0 / 0";
    elements.progressMessage.textContent = "正在提交";
    elements.progressBar.style.setProperty("--progress", "0");
    $(".progress-track").setAttribute("aria-valuenow", "0");
    renderRows();
  }

  async function startJob(event) {
    event.preventDefault();
    if (state.submitting || state.busyIds.size) return;
    showFormError();
    const enteredToken = $("#api-token").value.trim();
    if (enteredToken) state.apiToken = enteredToken;
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

    state.submitting = true;
    updateNavigation();
    if (state.jobId) {
      try {
        const current = await api(`/api/jobs/${state.jobId}`);
        if (!terminalStatuses.has(current.status)) {
          showFormError("当前任务仍在运行，请返回当前任务并等待完成，或先取消任务。");
          return;
        }
      } catch (error) {
        if (error.status !== 404) {
          showFormError(`无法确认当前任务状态：${error.message}。请先恢复当前任务。`);
          return;
        }
      } finally {
        state.submitting = false;
        updateNavigation();
      }
    }
    state.navigation += 1;
    state.submitting = true;
    state.viewRequest += 1;
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
      setLayout("results");
      setSidebar(false);
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
      scrollToWorkspace();
    } catch (error) {
      state.status = "error";
      setSubmitDisabled(false);
      showFormError(error.message);
      setStatus("error", "任务创建失败");
      setLayout("compose");
    } finally {
      state.submitting = false;
      updateNavigation();
    }
  }

  async function cancelJob() {
    if (!state.jobId) return;
    const jobId = state.jobId;
    const navigation = state.navigation;
    elements.cancel.disabled = true;
    try {
      await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      await refreshSnapshot();
    } catch (error) {
      showToast(error.message);
    } finally {
      if (jobId === state.jobId && navigation === state.navigation
        && !state.historyMode && !elements.cancel.hidden && !isTerminal()) {
        elements.cancel.disabled = false;
      }
    }
  }

  async function runNodeAction(action, id) {
    if (state.historyMode || !state.jobId || state.busyIds.has(id) || !isTerminal()) return;
    const row = state.rows.get(id);
    if (!row) return;

    if (action === "edit") {
      state.editingId = id;
      state.editDraft = row.name;
      renderRows();
      findEditInput(id)?.focus();
      findEditInput(id)?.select();
      return;
    }
    if (action === "cancel-edit") {
      state.editingId = null;
      renderRows();
      return;
    }
    if (action === "save") {
      const input = findEditInput(id);
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
      const jobId = state.jobId;
      const navigation = state.navigation;
      const request = ++state.rawRequest;
      const isCurrent = () => request === state.rawRequest
        && jobId === state.jobId && navigation === state.navigation && !state.historyMode;
      let raw;
      try {
        raw = await api(`/api/jobs/${jobId}/raw`);
      } catch (error) {
        if (!isCurrent()) return "";
        throw error;
      }
      if (!isCurrent()) return "";
      state.raw = raw;
    }
    elements.rawView.textContent = state.raw;
    elements.rawView.classList.remove("is-error");
    return state.raw;
  }

  async function activateViewTab(view, focusTab = false) {
    if (state.historyMode && view === "raw") return;
    state.view = view;
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focusTab) button.focus();
    });
    elements.tableView.hidden = view !== "table";
    elements.rawPanel.hidden = view !== "raw";
    if (view === "raw") {
      try {
        await fetchRaw(true);
      } catch (error) {
        elements.rawView.textContent = `原始 YAML 加载失败：${error.message}`;
        elements.rawView.classList.add("is-error");
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

  function setSourceMode(yamlMode) {
    const urlInput = $("#url");
    const yamlInput = $("#yaml");
    $("#url-pane").hidden = yamlMode;
    $("#yaml-pane").hidden = !yamlMode;
    urlInput.disabled = yamlMode;
    urlInput.required = !yamlMode;
    yamlInput.disabled = !yamlMode;
    yamlInput.required = yamlMode;
    $("#yaml-file").disabled = !yamlMode;
    showFormError();
  }

  document.querySelectorAll('input[name="kind"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      if (radio.checked) setSourceMode(radio.value === "yaml");
    });
  });
  setSourceMode(false);

  $("#mode").addEventListener("change", (event) => {
    const browserMode = event.target.value === "browser";
    $("#source").disabled = browserMode;
    $("#mode-help").hidden = !browserMode;
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
  function selectVisible(checked) {
    visibleRows().forEach((row) => {
      const id = Number(row.id);
      if (checked) state.selected.add(id);
      else state.selected.delete(id);
    });
    renderRows();
  }
  elements.selectAll.addEventListener("change", (event) => selectVisible(event.target.checked));
  elements.mobileSelectAll.addEventListener("change", (event) => selectVisible(event.target.checked));
  [elements.filterName, elements.mobileFilterName].forEach((input) => {
    input.addEventListener("input", (event) => {
      state.filters.name = event.target.value.trim();
      renderRows();
    });
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
  elements.mobileSort.addEventListener("change", () => {
    state.sort.key = elements.mobileSort.value;
    renderRows();
  });
  elements.mobileSortDirection.addEventListener("click", () => {
    state.sort.direction = state.sort.direction === "asc" ? "desc" : "asc";
    renderRows();
  });
  [
    [elements.filterType, "type"], [elements.mobileFilterType, "type"],
    [elements.filterNative, "native"], [elements.mobileFilterNative, "native"],
    [elements.filterSource, "source"], [elements.mobileFilterSource, "source"],
    [elements.filterStatus, "status"], [elements.mobileFilterStatus, "status"],
  ].forEach(([element, key]) => {
    element.addEventListener("change", () => {
      state.filters[key] = element.value;
      renderRows();
    });
  });
  elements.clearFilters.addEventListener("click", () => {
    resetFilters();
    renderRows();
  });
  function handleRowSelection(event) {
    const id = event.target.dataset.select;
    if (id === undefined) return;
    if (event.target.checked) {
      state.selected.add(Number(id));
    } else {
      state.selected.delete(Number(id));
    }
    renderRows();
  }
  function handleRowAction(event) {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    runNodeAction(button.dataset.action, Number(button.dataset.id));
  }
  function handleEditKey(event) {
    if (!event.target.matches("[data-edit-input]")) return;
    const id = Number(event.target.dataset.editInput);
    if (event.key === "Enter") runNodeAction("save", id);
    if (event.key === "Escape") runNodeAction("cancel-edit", id);
  }
  [elements.rows, elements.mobileResults].forEach((container) => {
    container.addEventListener("change", handleRowSelection);
    container.addEventListener("click", handleRowAction);
    container.addEventListener("keydown", handleEditKey);
    container.addEventListener("input", (event) => {
      if (event.target.matches("[data-edit-input]")) {
        state.editDraft = event.target.value;
        document.querySelectorAll(`[data-edit-input="${state.editingId}"]`).forEach((input) => {
          if (input !== event.target) input.value = state.editDraft;
        });
      }
    });
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => activateViewTab(button.dataset.view));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const tabs = [...document.querySelectorAll('[role="tab"]')]
        .filter((tab) => !tab.hidden && !tab.disabled);
      const current = tabs.indexOf(button);
      let next = current;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
      event.preventDefault();
      activateViewTab(tabs[next].dataset.view, true);
    });
  });
  $("#refresh-raw").addEventListener("click", async () => {
    try {
      await fetchRaw(true);
    } catch (error) {
      elements.rawView.textContent = `原始 YAML 加载失败：${error.message}`;
      elements.rawView.classList.add("is-error");
    }
  });
  $("#copy-yaml").addEventListener("click", () => handleExport("copy"));
  $("#export-yaml").addEventListener("click", () => handleExport("yaml"));
  $("#export-csv").addEventListener("click", () => handleExport("csv"));
  $("#open-clash").addEventListener("click", openInClash);
  elements.refreshHistory.addEventListener("click", loadHistory);
  elements.returnCurrent.addEventListener("click", returnCurrentJob);
  elements.emptyStateAction.addEventListener("click", async () => {
    if (state.status === "unknown" && state.jobId) {
      try {
        const snapshot = await refreshSnapshot();
        if (snapshot && !terminalStatuses.has(snapshot.status)) connectEvents();
        return;
      } catch (error) {
        showToast(error.message);
      }
    }
    setLayout("compose");
    const selectedKind = $('input[name="kind"]:checked')?.value;
    (selectedKind === "yaml" ? $("#yaml") : $("#url")).focus();
  });
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
    const navigation = state.navigation;
    setLayout("results");
    try {
      const snapshot = await refreshSnapshot();
      if (snapshot && !terminalStatuses.has(snapshot.status)) connectEvents();
    } catch (error) {
      if (navigation !== state.navigation) return;
      if (error.status !== 404) {
        setStatus("unknown", "请检查 API 令牌或连接后重试读取当前任务");
        setLayout("compose");
        $(".advanced").open = true;
        showFormError(error.status === 401 ? "此服务需要 API 令牌。填写后可返回当前任务。" : "暂时无法读取当前任务，任务 ID 已保留。请稍后重试。");
        return;
      }
      rememberJob(null);
      state.jobId = null;
      setStatus("unknown", "上次任务已过期，可以创建新任务");
      setSubmitDisabled(false);
      renderRows();
    }
  }

  function updateNavigation() {
    $("#current-task").hidden = !state.jobId || (state.layout === "results" && !state.historyMode);
    $("#close-composer").hidden = !state.jobId && !state.historyMode;
    $("#new-check").disabled = state.submitting || state.busyIds.size > 0;
  }

  function setLayout(layout) {
    state.layout = layout;
    $("#main").dataset.layout = layout;
    updateNavigation();
  }

  function setSidebar(open) {
    document.body.classList.toggle("sidebar-open", open);
    elements.sidebarToggle.setAttribute("aria-expanded", String(open));
    elements.sidebarBackdrop.hidden = !open;
    const mobile = window.matchMedia("(max-width: 800px)").matches;
    $(".main-shell").inert = mobile && open;
    if (open) $("#sidebar-close").focus();
    else if (mobile && elements.sidebar.contains(document.activeElement)) elements.sidebarToggle.focus();
  }

  function composeNew() {
    if (state.submitting || state.busyIds.size) return;
    state.viewRequest += 1;
    setLayout("compose");
    setSidebar(false);
    showFormError();
    ($('input[name="kind"]:checked').value === "yaml" ? $("#yaml") : $("#url")).focus();
  }
  $("#new-check").addEventListener("click", composeNew);
  $("#edit-config").addEventListener("click", composeNew);
  $("#close-composer").addEventListener("click", () => { setLayout("results"); scrollToWorkspace(); });
  $("#current-task").addEventListener("click", returnCurrentJob);
  elements.sidebarToggle.addEventListener("click", () => setSidebar(true));
  $("#sidebar-close").addEventListener("click", () => setSidebar(false));
  elements.sidebarBackdrop.addEventListener("click", () => setSidebar(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (document.body.classList.contains("sidebar-open")) setSidebar(false);
      elements.exportMenu.open = false;
    }
    if (event.key === "Tab" && document.body.classList.contains("sidebar-open")) {
      const controls = [...elements.sidebar.querySelectorAll("a[href], button:not(:disabled)")].filter((node) => node.offsetParent !== null);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  window.matchMedia("(max-width: 800px)").addEventListener("change", () => setSidebar(false));

  renderRows();
  loadHistory();
  restoreJob();
})();
