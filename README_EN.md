# Clash IP Checker

FastAPI + Mihomo subscription IP checker. Mihomo 1.19.18; amd64/arm64 images use Playwright's server headless shell for browser mode.

## Compose quick start

```bash
docker compose config
docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health
docker compose logs --tail=200 clash-checker
```

Compose pulls `ghcr.io/leeflouring/clash-ip-checker:latest` by default. Pushes to `main` publish `latest`, `main`, and `sha-...` image tags for amd64 and arm64; `v*` Git tags publish version tags. If the GHCR package is private, log in with a token granting `read:packages` before pulling. Only host port 8000 is published. Mihomo proxy/controller use loopback 7890/9090 with no DNS listener. Named volume `clash-data` mounts at `/data` (`/data/jobs`, `/data/mihomo`); UID 10001, read-only rootfs, `restart: unless-stopped`.

Open [`http://127.0.0.1:8000/`](http://127.0.0.1:8000/). For a remote deployment, replace `127.0.0.1` with the server's real address; do not browse to the bind address `0.0.0.0`. Submit a URL, pasted YAML, or uploaded YAML; choose fast/browser. Results support header filters and sorting, failure reasons, table or raw YAML, selected/all YAML or CSV export, copy, and Clash deep-link. Terminal results are retained as read-only snapshots in `/data/jobs/history.json`; view or delete them from the page, with at most `MAX_JOBS` records. A blank skip-keyword field uses the server defaults, so subscription metadata nodes such as remaining traffic, reset, and expiry are skipped. After completion edit/delete/recheck nodes; cancel jobs and reconnect polling. `/ipcheck` and legacy `GET /check?url=` remain supported; checks cache by content plus effective options. The container has no GUI or desktop environment: browser mode is always headless and headed mode is unsupported. The headless shell is smaller than full Chromium, but the image is still significantly larger than a fast-only base image. One Mihomo worker limits throughput and browser mode uses more memory. Only job IDs are stored in sessionStorage; URLs and API tokens are not persisted.

With fallback enabled, fast mode tries the other Ping0/IPPure source after the selected primary, then makes an anonymous request to the third-party IPQuery service. A browser parse failure also enters this fast fallback chain. IPQuery reports risk and datacenter/mobile properties but cannot establish native versus broadcast allocation, so native status is shown as unknown. IPQuery is not contacted when fallback is disabled.

Environment defaults: `DATA_DIR=/data/jobs`, `CONFIG_PATH=/app/config.yaml`, `CLASH_API_URL=http://127.0.0.1:9090`, `PORT=8000`, `MAX_QUEUE_SIZE=10`, `MAX_AGE=360`, `REQUEST_TIMEOUT=15`, `SOURCE=ping0`, `FALLBACK=true`, `MAX_SUBSCRIPTION_BYTES=5242880`, `MAX_REDIRECTS=3`, `JOB_TTL=3600`, `MAX_JOBS=100`, `ALLOW_PRIVATE_SUBSCRIPTIONS=false`, `SHOW_ADVANCED_SETTINGS=false`, `API_TOKEN=`. `MAX_JOBS` also caps persisted history. API token protects job/result/subscription/history routes via Bearer; health, static assets, and UI config remain public. The UI keeps the token in memory and uses authenticated polling. Clash deep links and direct `/check` subscriptions cannot carry Bearer authentication, so download/import YAML or provide suitable authentication at a TLS reverse proxy. Private SSRF targets and URL credentials are rejected by default. History stores only masked labels, terminal statistics, and public result rows—never subscription URLs, API tokens, raw YAML, proxy configuration, or credentials.

Back up the Compose volume `clash-data`, then update the `main` checkout and run `docker compose pull && docker compose up -d`. Roll back by setting `IMAGE_TAG` to a previous `sha-...` tag and running `docker compose up -d`; restore the volume if needed. Migrate legacy `./data` into `/data/jobs` first. Restrict firewall sources for public deployments. See [DEPLOY_GCP.md](./DEPLOY_GCP.md).

```bash
docker build -t ghcr.io/leeflouring/clash-ip-checker:local .
IMAGE_TAG=local docker compose up -d --pull never
```
