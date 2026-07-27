# Docker host baseline (2026-07-27)

## Status

Docker CLI and Compose are installed, but Docker Desktop's Linux daemon is not running. No image/container/volume mutations were performed.

## Observed host/tooling

- `docker version`: client 29.6.1, API 1.55, Go 1.26.4, OS/Arch `windows/amd64`, context `desktop-linux`. Server query failed: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified`.
- `docker compose version`: v5.2.0.
- `docker buildx version`: v0.35.0-desktop.2.
- `docker info`, `docker images`, and `docker ps -a` all fail with the same unavailable Linux-engine pipe; server architecture/OS, existing images, containers, and volumes therefore could not be inspected.
- Port check (`Get-NetTCPConnection -LocalPort 8000 -State Listen`) returned no listener; no owning process was found.

## Current Compose baseline (read-only `docker compose config`)

Config succeeds client-side and resolves project name `clash-ip-checker`; service `clash-checker` builds `D:\Work\clash-ip-checker\Dockerfile`, image `clash-checker`, publishes only `8000:8000`, bind-mounts `D:\Work\clash-ip-checker\data` to `/root/.config/mihomo/data`, sets `TZ=Asia/Shanghai` and `DATA_DIR=/root/.config/mihomo/data`, and uses `restart: always`. The generated default network is `clash-ip-checker_default`.

Source security/runtime gaps visible in baseline:

- [Dockerfile:1] `python:3.10-slim-bookworm` has no platform pin/matrix.
- [Dockerfile:22] downloads amd64-only Mihomo v1.19.18 without checksum verification.
- [Dockerfile:31-34] downloads data into `/root/.config/mihomo`.
- [docker-compose.yml:7-12] uses host bind mount and root-path `DATA_DIR`.
- [entrypoint.sh:12-22] generates `external-controller: 0.0.0.0:9090` and DNS `listen: 0.0.0.0:53`; [entrypoint.sh:27] backgrounds Mihomo; [entrypoint.sh:31] waits with timeout but does not fail on timeout; [entrypoint.sh:36] starts Uvicorn without supervision/trap.

## Collision/project-name guidance

Because daemon-side `docker ps`/volume inspection is unavailable, same-name resource collisions remain unknown. Once Docker Desktop is running, use a unique project name for this task, e.g. `clash-ip-checker-delivery-20260727`, on every command:

```powershell
docker compose -p clash-ip-checker-delivery-20260727 config
docker compose -p clash-ip-checker-delivery-20260727 build
docker compose -p clash-ip-checker-delivery-20260727 up -d
docker compose -p clash-ip-checker-delivery-20260727 ps
docker volume ls --filter label=com.docker.compose.project=clash-ip-checker-delivery-20260727
```

Before start, check host port and resources without mutation:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
docker ps --format '{{.Names}} {{.Ports}}'
docker volume ls
```

## Non-destructive E2E acceptance commands

After daemon startup and implementation changes, run exactly:

```powershell
docker version
docker info
docker buildx ls
docker compose -p clash-ip-checker-delivery-20260727 config
docker compose -p clash-ip-checker-delivery-20260727 build --progress=plain
docker compose -p clash-ip-checker-delivery-20260727 up -d
docker compose -p clash-ip-checker-delivery-20260727 ps
curl.exe -fsS http://127.0.0.1:8000/health
docker compose -p clash-ip-checker-delivery-20260727 logs --tail=200 clash-checker
docker inspect --format '{{.Config.User}} {{json .Config.Healthcheck}} {{json .NetworkSettings.Ports}}' clash-ip-checker-delivery-20260727-clash-checker-1
docker exec clash-ip-checker-delivery-20260727-clash-checker-1 id
docker exec clash-ip-checker-delivery-20260727-clash-checker-1 sh -c 'ss -lntup || netstat -lntup'
docker compose -p clash-ip-checker-delivery-20260727 top
```

Do not execute stop/kill/recreate in this baseline. For the requested failure/TERM checks, the delivery runner must explicitly perform controlled `docker compose ... kill`/`stop` only after capture and consent in its execution phase.

## Controlled fixtures to create (outside image and non-secret)

- A local HTTP fixture serving deterministic Clash YAML and URL payloads (loopback-only, fixed ports), with one valid proxy and one intentionally invalid endpoint to exercise progress/error paths.
- Small YAML fixtures covering table/raw output, selective proxy options, and browser-mode input; CSV export expected values checked against fixture rows.
- A temporary test data directory/volume marker file (e.g. `/data/e2e-marker`) to verify persistence across recreation; do not put credentials or real subscriptions in fixtures.
- Record cache reuse by submitting the same fixture twice and compare result metadata; submit again with differing options to prove isolation.

## Gaps/constraints

- Server architecture, build platforms, current images/containers/volumes, and daemon-side port ownership are unverified until Docker Desktop Linux engine starts. `docker buildx ls` and `docker compose build` cannot be run now.
- Existing bind-mounted `./data` may contain user state; inspect before any migration and avoid deleting it.
