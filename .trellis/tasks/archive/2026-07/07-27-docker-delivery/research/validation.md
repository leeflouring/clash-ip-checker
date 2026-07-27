# Docker Delivery Validation

Date: 2026-07-27

## Static and automated checks

- `python -m compileall -q main.py core tests`: PASS.
- `python -m unittest discover -s tests -v`: PASS, 19 tests.
- `node --check static/js/app.js`: PASS.
- `bash -n entrypoint.sh`: PASS.
- `docker compose config --quiet`: PASS.
- `git diff --check`: PASS; only expected Windows LF/CRLF notices.
- Regression coverage includes headed-browser rejection, active/completed cache
  reuse, option isolation, SSRF redirects/size/deadline, queue admission,
  cancellation, edit/delete/recheck, raw YAML, selective YAML, and CSV quoting.

## Image and architecture

- Final amd64 image build: PASS.
- Running image inspection: `262609996` bytes (about 263 MB distribution
  size).
- Earlier full-browser image measured 2.93 GB locally; the final image uses
  Chromium headless shell only and does not install Xvfb.
- Container browser launch: `HEADLESS_BROWSER_OK 148.0.7778.96`.
- ARM64 Mihomo downloader ran on `$BUILDPLATFORM`, selected
  `mihomo-linux-arm64-v1.19.18.gz`, and passed the pinned SHA-256 check.
- The local Docker builder advertised arm64 but its target-architecture
  execution returned `exec format error`; a full arm64 final-stage build
  therefore requires native arm64 or a working binfmt/QEMU builder. No host
  binfmt state was changed.

## Runtime and least privilege

- Running project: `clash-ip-checker-delivery-20260727`.
- Service is healthy at `http://127.0.0.1:8000`.
- `/health`: HTTP 200 with Mihomo ready; `/ipcheck`: HTTP 200.
- Runtime identity: `uid=10001(app) gid=10001(app)`.
- Root filesystem is read-only. `/data` is the persistent named volume and
  `/tmp` is tmpfs; `/app` is not writable.
- Compose publishes only port 8000. `/proc/net/tcp` showed 7890 and 9090 bound
  to `127.0.0.1`; 8000 is the Web listener.
- Capabilities are dropped and `no-new-privileges` is enabled.
- Process tree contains Bash PID 1 with Mihomo and Uvicorn children.

## Controlled E2E

The fixture is `research/fixtures/e2e.yaml` with two intentionally unreachable
local SOCKS5 nodes.

- YAML job reached `completed` with two structured table rows.
- Identical content/options reused Job ID
  `9e08aeff6f574ca5aeb3378d51e6606f`.
- Changing only `source` produced independent Job ID
  `d1ae3833d295412586f709760fc19ac5`.
- Rename to `renamed, "quoted"` survived raw YAML and CSV quoting.
- Selective YAML/CSV, delete, and single-node recheck passed.
- Cancellation reached terminal `cancelled`.
- With `API_TOKEN=e2e-token`, missing credentials returned 401, valid Bearer
  credentials reached the route, and `/health` stayed public; the default empty
  token was restored.
- Private fixture URL was rejected by default. With the explicit private-source
  opt-in it completed two rows and exposed only the masked label
  `host.docker.internal / … / e2e.yaml`; the secure default was restored.

## Lifecycle and persistence

- Killing Mihomo made `/health` return 503, triggered graceful Uvicorn shutdown,
  exited the supervisor, and Compose restarted the container
  (`RestartCount=1` during that run).
- `docker compose stop -t 20` exited the container with status 143 and no
  orphaned child; it restarted healthy afterward.
- The named `/data` volume survived repeated recreates and API-token/private-URL
  configuration toggles.
- The final rebuilt container remains healthy with default secure settings.

## Web entry regression

- The original image served the UI only at `/ipcheck`; `GET /` returned 404.
- `GET /` and `GET /ipcheck` now share the same `FileResponse` and both return
  the identical 8,948-byte workspace page with HTTP 200.
- `GET /health` remains HTTP 200 with Mihomo ready.
- Browsing to `0.0.0.0` was reproduced as HTTP 502 through the host proxy.
  Documentation now distinguishes bind addresses from valid client addresses.
- Local verified URL: `http://127.0.0.1:8000/`.

## Table filters and failure diagnosis

- Real saved result summary: 49 nodes, 20 failed before this repair. Three
  failures were subscription metadata names containing remaining/reset/expiry
  keywords and should have been skipped.
- Root cause: the Web client sent an empty `skip_keywords` string, overriding
  the server default with an empty list. Blank input is now omitted.
- Real Docker regression fixture returned `skipped=1, failed=1`, proving a
  metadata pseudo-node is skipped while a genuinely unreachable node remains
  failed.
- Header filters cover name, property, native status, source, and row status.
  Headless browser assertions passed for name/status filtering, zero-match
  state, visible-row select-all, hidden selection retention, and zero console
  errors.
- Mobile `390x844` screenshot inspection passed with horizontal table scrolling.
- Failure rows now expose a short human-readable reason and a sanitized full
  tooltip. URLs and Authorization/API-key/token/secret/password values are
  redacted, including Bearer and Basic credentials.
- Final automated checks: 19 tests, Python compile, JS syntax, Compose config,
  and diff check passed. Final rebuilt container is healthy, `/` returns 200,
  filters are present, and image distribution size is `262609996` bytes.
