# Validation Evidence

## 2026-07-27 — Success fallback and sortable headers

- `python -m unittest discover -s tests -v`: 21 passed.
- `python -m compileall main.py core`: passed.
- `node --check static/js/app.js`: passed.
- `docker compose config --quiet`: passed.
- `git diff --check`: passed (line-ending warnings only).
- Built and recreated `clash-checker:latest`; final size `262611294` bytes.
- Container `clash-ip-checker-delivery-20260727-clash-checker-1` is healthy,
  serves `/` with HTTP 200, reports Mihomo ready, and runs as UID 10001.
- Chromium headless shell launched inside the image without a display server.
- Live IPQuery response passed global-IP/risk/schema validation.
- On six isolated old-failure nodes, the full chain returned six valid results;
  four required IPQuery in the latest run. The temporary container and copied
  subscription fixture were removed afterward.
- Playwright verified name ascending/descending order, filter composition,
  `aria-sort`, and an enabled fallback checkbox in browser mode.

Provider contract research used official documentation:

- <https://ipquery.io/>
- <https://ipapi.is/developers.html> (evaluated but not selected)

## 2026-07-27 — Persistent detection history

- `python -m unittest discover -s tests -v`: 28 passed.
- `python -m compileall main.py core tests`: passed.
- `node --check static/js/app.js`: passed.
- `docker compose config --quiet`: passed.
- `git diff --check`: passed (line-ending warnings only).
- Rebuilt `clash-checker:latest`; final size `262620159` bytes.
- A controlled three-node terminal job was persisted to
  `/data/jobs/history.json`; its unique proxy password was absent from the
  history file.
- `docker compose up -d --force-recreate` retained the history record through
  the named volume while the former live-job endpoint correctly returned 404.
- A fresh headless Chromium session opened the persisted snapshot at 390x844,
  kept filters and name sorting usable, disabled all selection/mutation/export
  actions, hid raw YAML, and hid `返回当前任务`.
- UI deletion removed the exact history record; GET then returned 404 and the
  persisted JSON remained valid.
- The recreated service remained healthy on `0.0.0.0:8000`, ran as user
  `app` (UID 10001), and kept a read-only root filesystem.
