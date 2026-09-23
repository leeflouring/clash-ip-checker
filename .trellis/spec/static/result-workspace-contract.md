# Docker Result Workspace Contract

## 1. Scope / Trigger

Read this contract when changing the Docker HTML/CSS/JS, `/api/jobs` routes,
`/api/history` routes, node result schema, YAML/CSV export, browser-mode UI, or
history presentation. The compatibility subscription endpoints are separate
and must remain usable.

## 2. Signatures

- `POST /api/jobs`: JSON contains exactly one non-empty `url` or `yaml`, plus
  `options`; returns `job_id`, `status`, `total`, and a privacy-safe `label`.
- `GET /api/jobs/{job_id}` and `/events`: return the same job snapshot.
- Node actions: `PUT` and `DELETE /nodes/{id}`, then
  `POST /nodes/{id}/recheck`.
- `GET /raw`: current Clash YAML after edits/deletes.
- `POST /export`: `{node_ids: number[]}`; an empty list means all current
  nodes; returns YAML and UTF-8 BOM CSV strings plus filenames.
- `GET /api/history`: returns `{records: HistorySummary[]}` where each summary
  contains `job_id`, privacy-safe `label`, terminal `status`, `finish_time`,
  `total`, `checked`, `failed`, and `skipped`.
- `GET /api/history/{job_id}`: returns one sanitized terminal snapshot.
- `DELETE /api/history/{job_id}`: returns
  `{status: "deleted", job_id}` after the atomic deletion is persisted.
- Client sort state: `{key: "id" | <sortable result field>, direction:
  "asc" | "desc"}`; a new job resets to `{key: "id", direction: "asc"}`.

## 3. Contracts

- `job_id` is the only task value stored in `sessionStorage`. Never persist,
  log, or render a plaintext subscription URL.
- Node IDs are stable original proxy indexes. Deletes never renumber remaining
  rows.
- Table view is the default. SSE snapshots update rows idempotently; GET
  snapshots restore state after reload and before reconnect.
- Table headers provide native filters for node name, property, native status,
  source, and row status. Filters change only visible rows; statistics still
  describe the full job and exports still use the explicit selection.
- Node name, IP, risk, shared/Bot, property, native, source, and status headers
  are native buttons. Sorting runs after filtering, toggles ascending/descending,
  and uses numeric node ID as the deterministic tie-break. The active `th`
  exposes `aria-sort`; arrows are presentational only.
- Select-all affects only currently visible rows. Hidden selections remain
  selected, and a zero-match filter renders an explicit table message.
- EventSource retries at most three times and never discards partial rows.
- Edit, delete, and recheck require a terminal job. All Mihomo checks share the
  global execution lock; mutations and raw/export snapshots use the per-job
  lock.
- YAML rebuilding applies current names, removes deleted/unselected proxies,
  rewrites proxy-group members, and preserves built-in/group references.
- CSV uses a real CSV writer so commas, quotes, and newlines remain valid.
- Browser mode is an explicit option, not an alias for the fast source.
  Selecting it disables only the fast primary-source selector; the fallback
  checkbox remains available because browser failures may enter the fast chain.
- History routes use the same Bearer-token middleware as job routes. History
  identifiers are opaque UUID4 hex strings; no URL or source hash is exposed.
- Opening a history item fetches the full snapshot before closing any current
  SSE stream. A failed fetch therefore leaves the current live task intact.
- History uses the existing result table, statistics, filters, and sorting.
  History mode is read-only: hide the raw-YAML tab and disable selection,
  rename/delete/recheck, copy/download/export, and Clash-open actions.
- The page is a light product workspace: a secondary navigation/history sidebar,
  a centered configuration composer while idle, and a wide result workspace
  after submission. New configuration stays easily reachable without silently
  losing a running task. On mobile the sidebar becomes an accessible drawer.
  This 2026-09-22 direction supersedes the earlier dark-console layout.
- The history badge equals the number of successfully loaded summaries. A
  history snapshot closes the mobile drawer only after the snapshot is fetched
  and applied; a failed fetch leaves the current job and SSE connection intact.
  Desktop history remains available in the sidebar.
- Desktop rows and mobile result cards are two renderers of the same filtered,
  sorted row list. Their filter, sort, selection, edit, and history-read-only
  controls must write to the same `state`; do not maintain a second mobile
  result model.
- Reconcile rows by stable numeric ID and public row content. An unchanged
  snapshot must retain the existing DOM elements, including a focused edit
  draft, checkbox focus and open mobile details. When a row changes, update
  only that row in both renderers and restore the active editor where possible.
- Guard asynchronous job/history/raw responses with the current job or
  navigation generation before applying them. Fetch history before closing
  the current stream. Before a new submission replaces the current job,
  confirm its latest server state is terminal; a failed status check must
  preserve the active job ID and results.
- Raw fetches must also compare their own request sequence: an old success or
  error after history→current navigation cannot overwrite a newer raw result.
  If a cancel API request fails, restore the cancel control while the task is
  still running so the user can retry. Changed-only SSE traffic cannot be
  relied on to re-enable a disabled control after an unchanged task state.
- The document must not scroll horizontally at 320px or wider. Wide result
  tables and raw YAML stay inside their named, keyboard-focusable local scroll
  regions instead of clipping the page root.
- Keep native input validation available. When URL/YAML modes or browser mode
  change, update both `disabled` and `required` so inactive controls cannot
  block submission and the active source remains browser-validatable.
- The workspace uses a coherent light theme with `<meta name="color-scheme"
  content="light">`, warm neutral surfaces, graphite text and restrained accent
  colors. System color-scheme preferences must not invert individual sections.
  Primary targets are at least 40px high and all other interactive targets are
  at least 24px in both dimensions.
- `返回当前任务` is shown only when the current browser session still has an
  active job ID. A fresh session may inspect history without fabricating a live
  task. Starting a new job exits history mode.
- History is loaded on startup, on explicit refresh, and after terminal job
  transitions. Delete requires confirmation, refreshes the list, and exits a
  deleted snapshot safely.

## 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Both/neither `url` and `yaml` are non-empty | HTTP 400 |
| Invalid mode, source, booleans, timeout, or cache age | HTTP 400 |
| Pasted YAML exceeds the configured byte limit | HTTP 413 |
| Job queue is full | HTTP 503 |
| Unknown job/node | HTTP 404 |
| Mutation before terminal state | HTTP 409 |
| Duplicate/empty/overlong edited name | HTTP 409 / 400 |
| SSE fails three times | Keep current rows and show `unknown` |
| Filters match no rows | Keep the table header and show a no-match row |
| A sortable header is clicked | New key starts ascending; the active key toggles direction and updates `aria-sort` |
| A new job starts | Clear filters and restore ID-ascending order |
| Missing/invalid Bearer token on a protected history route | HTTP 401 |
| Unknown history ID | GET/DELETE returns HTTP 404 |
| History deletion cannot be persisted | HTTP 500; record remains visible after refresh |
| History snapshot fetch fails | Preserve the current job and SSE connection |
| History list loads | Badge equals `records.length`; empty list shows `0` |
| History snapshot applies successfully | Enter read-only mode, then close the mobile navigation drawer |
| History snapshot is open | Table/filter/sort remain usable; every mutation/export/raw action is unavailable |
| Viewport is 320px or wider | `documentElement.scrollWidth <= documentElement.clientWidth`; only named table/raw regions may scroll horizontally |

## 5. Good / Base / Bad Cases

- Good: reload a running task; GET restores rows, SSE resumes, and no URL is in
  storage.
- Good: export one renamed node whose name contains a comma and quote; CSV and
  YAML parse correctly and proxy-groups contain the new name.
- Good: filter failed nodes, select all visible rows, then clear the filter;
  hidden selections remain intact.
- Good: filter one source, sort risk descending, and export the explicit
  selection; only presentation order changes.
- Base: no selected rows exports every non-deleted node.
- Base: equal sort values retain deterministic numeric-ID order.
- Good: after container recreation, a fresh browser opens a persisted history
  snapshot, filters and sorts it at 390px, and all mutation/export controls stay
  disabled.
- Good: the history badge shows the loaded summary count; selecting one applies
  the snapshot and closes the mobile drawer without losing the current job ID.
- Good: a mobile filter or sort change immediately produces the same visible
  node IDs and selection state as the desktop table after resizing.
- Base: with no retained records, the history section says `暂无历史记录`.
- Bad: close the current EventSource before a history fetch succeeds, leaving
  a live task disconnected when the history record is stale.
- Bad: key rows by node name; a rename then creates duplicates or loses
  selection.
- Bad: concatenate CSV with commas or export while an edit is midway through
  its atomic file update.

## 6. Tests Required

- Start from pasted YAML without network or browser access and reach a terminal
  snapshot through a fake checker.
- Edit, selectively export, delete, read raw YAML, and single-node recheck.
- Parse exported CSV and YAML rather than comparing only substrings.
- Check group membership after selection, rename, and delete.
- Verify ambiguous input and invalid modes are rejected.
- Check local static assets and privacy-safe URL labels.
- Check filter controls, visible-row selection, no-match state, and reset on a
  new job at desktop and 390px widths.
- Click a header twice and assert row order, arrow/`aria-sort`, filter
  composition, and stable ID tie-break. In browser mode assert fallback remains
  enabled.
- Protect history list/get/delete with the configured API token; assert 401,
  404, and deletion-write 500 behavior.
- In a fresh browser session, open history and assert the raw tab, row
  checkboxes/actions, export/copy, and Clash-open controls are hidden/disabled;
  filters and sorting still work, and `返回当前任务` is hidden.
- Assert history badge count after empty/non-empty loads, mobile drawer close
  after a successful snapshot, and current-SSE preservation on failure.
- With a current live task, make a history fetch fail and assert the current
  event stream is not closed.
- Run JS syntax, Python compile, complete unit tests, desktop/mobile browser
  screenshots, network status, and console inspection.
- At 1440px, 1024px, 768px, 390px, and 320px while emulating both system color-scheme
  preferences, assert the light workspace remains consistent and has no
  document horizontal overflow. Exercise filters, sorting, selection, read-only
  history, raw errors, touch-target sizes, and the export popover; local
  table/raw scrolling is allowed.

## 7. Wrong vs Correct

### Wrong

```javascript
localStorage.setItem("subscription_url", url);
rows.set(node.name, node);
```

### Correct

```javascript
sessionStorage.setItem("active_job_id", jobId);
rows.set(Number(node.id), node);
```

### Wrong

```javascript
rows.sort(compareRows).filter(matchesFilters);
```

### Correct

```javascript
rows.filter(matchesFilters).sort(compareRows);
```

### Wrong

```javascript
closeEvents();
const snapshot = await api(`/api/history/${jobId}`);
```

### Correct

```javascript
const snapshot = await api(`/api/history/${jobId}`);
closeEvents();
setHistoryMode(true);
applySnapshot(snapshot, true);
setSidebar(false);
```

### Wrong

```javascript
renderDesktopRows(visibleRows());
renderMobileRows(visibleMobileRows()); // Separate filters and selection drift.
```

### Correct

```javascript
const rows = visibleRows();
renderDesktopRows(rows);
renderMobileRows(rows); // One normalized state, two responsive renderers.
```
