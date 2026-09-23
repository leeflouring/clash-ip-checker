# Docker Runtime Contract

## 1. Scope / Trigger

Read this contract before changing `Dockerfile`, `.dockerignore`,
`docker-compose.yml`, `entrypoint.sh`, runtime dependency pins, `/health`, or
browser-mode deployment. The service must work on a server with no display
server or installed desktop browser.

## 2. Signatures

- Deploy: `docker compose pull && docker compose up -d` from the `main` branch; Compose uses the GHCR image, with `IMAGE_TAG` for version selection.
- Local build: `docker build -t ghcr.io/leeflouring/clash-ip-checker:local .` and `IMAGE_TAG=local docker compose up -d --pull never`.
- Web UI: `GET /` and `GET /ipcheck` both return the workspace page. Clients
  use `127.0.0.1`, `localhost`, or the server's real address, never `0.0.0.0`.
- Health: `GET /health -> 200 {"status":"healthy","mihomo":"ready"}` only
  when FastAPI and the loopback Mihomo controller are ready; otherwise `503`.
- Entrypoint: `/app/entrypoint.sh` is PID 1, starts Mihomo first, waits for its
  controller, starts Uvicorn, forwards `TERM`/`INT`, and exits when either child
  exits.
- Browser launch: Playwright Chromium is always `headless=True`; API requests
  with `mode=browser` and `headless=false` return HTTP 400.
- Architectures: Docker `TARGETARCH` accepts `amd64` and `arm64`; the downloader
  runs on `$BUILDPLATFORM` and selects the matching pinned Mihomo asset.

## 3. Contracts

- Base image: pinned Python minor on Debian slim.
- Runtime dependencies and Mihomo version are exact pins. The selected Mihomo
  archive must pass its architecture-specific SHA-256 check before extraction.
- Install `playwright install --only-shell chromium`; do not install a full
  desktop Chromium, Xvfb, or a window manager.
- Runtime user is UID 10001 (`app`). Root filesystem is read-only in Compose;
  writable paths are the named `/data` volume and `/tmp` tmpfs.
- Publish only `8000/tcp`. Mihomo controller `9090` and mixed proxy `7890`
  listen on `127.0.0.1` inside the container.
- `SAFE_PATHS` includes `DATA_DIR`, because Mihomo otherwise rejects job YAML
  files outside its home directory.
- Environment defaults:

| Key | Default / behavior |
|---|---|
| `DATA_DIR` | `/data/jobs` |
| `MIHOMO_HOME` | `/data/mihomo` |
| `CLASH_API_URL` | `http://127.0.0.1:9090` |
| `CONFIG_PATH` | `/app/config.yaml` |
| `PORT` | `8000` |
| `API_TOKEN` | empty; set to protect job/subscription routes |
| `ALLOW_PRIVATE_SUBSCRIPTIONS` | `false`; opt in only for trusted LAN sources |

## 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Unsupported `TARGETARCH` | Image build fails before downloading Mihomo |
| Mihomo digest mismatch | Image build fails |
| Mihomo readiness deadline expires | Entrypoint exits non-zero; Uvicorn does not start |
| Mihomo dies after startup | `/health` returns 503 and PID 1 terminates the container |
| Uvicorn exits | PID 1 terminates Mihomo and exits |
| Headed browser requested | HTTP 400 |
| Browser executable/runtime library missing | Browser job errors without breaking fast mode or PID 1 |
| Rootfs write outside `/data` or `/tmp` | Operation fails |
| Client browses to `0.0.0.0:8000` | Invalid destination or proxy 502; use loopback or the server's real address |

## 5. Good / Base / Bad Cases

- Good: a headless Linux server runs fast and browser modes using only the
  image-bundled Chromium headless shell.
- Base: `docker compose pull && docker compose up -d`, one named volume, one published port,
  healthy non-root service.
- Bad: expose Mihomo controller or mixed proxy on `0.0.0.0`.
- Bad: use `playwright install --with-deps chromium`, which installs a full
  browser/Xvfb stack and materially enlarges the image.
- Bad: background Mihomo with `&` and `exec uvicorn`; Mihomo death then leaves a
  healthy-looking but unusable API.

## 6. Tests Required

- `docker compose config --quiet` and an amd64 image build pass.
- ARM64 downloader selects the arm64 asset and passes SHA-256; a full ARM64
  build runs on native ARM64 or a builder with binfmt/QEMU.
- `/`, `/ipcheck`, and `/health` return 200 after startup.
- Inspect UID, read-only rootfs, capabilities, writable mounts, published ports,
  and `/proc/net/tcp` listener addresses.
- Launch Playwright Chromium headless inside the built image.
- Exercise YAML and URL jobs, cache/option isolation, progress, cancel,
  edit/delete/recheck, raw YAML, selective YAML, and CSV.
- Kill Mihomo and observe 503 plus container restart; stop and observe both
  children reaped; recreate and verify `/data` persistence.

## 7. Wrong vs Correct

### Wrong

```dockerfile
RUN playwright install --with-deps chromium
```

### Correct

```dockerfile
RUN apt-get install -y --no-install-recommends <measured-runtime-libraries> \
    && playwright install --only-shell chromium
```

The correct form keeps browser mode functional on display-less servers while
avoiding full-browser and Xvfb layers.

```text
Wrong:   http://0.0.0.0:8000/
Correct: http://127.0.0.1:8000/          # same machine
Correct: http://<server-address>:8000/   # remote machine
```
