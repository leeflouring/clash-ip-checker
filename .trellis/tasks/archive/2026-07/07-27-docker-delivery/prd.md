# Modernize container delivery and documentation

## Goal

Ship the integrated app as a reproducible, portable, least-privilege Docker
service with accurate setup/upgrade documentation and recorded E2E evidence.

## Requirements

- Support native amd64 and arm64 image builds.
- Pin Python dependencies and Mihomo; verify downloaded binary integrity.
- Install Playwright Chromium required by optional browser mode.
- Bind Mihomo controller/mixed proxy to loopback, remove unused DNS exposure,
  and publish only the Web port.
- Run application and Mihomo as an unprivileged user with one `/data` volume.
- Supervise both processes, forward shutdown signals, fail on Mihomo readiness
  failure, and expose an application health check.
- Add a minimal `.dockerignore` and Compose health/restart/volume configuration.
- Reconcile config defaults, environment names, and docs.
- Persist the latest terminal detection snapshots under `/data` and provide a
  privacy-safe Web history list with view and delete actions.
- Update README/README_EN, changelog and deployment/troubleshooting material for
  URL/YAML/table/CSV/browser workflows and security defaults.
- Build, start, inspect, exercise, stop, and recreate the actual Docker service.

## Acceptance Criteria

- [ ] `docker compose config` and image build succeed on the current Docker
      machine; architecture mapping is explicit for amd64/arm64.
- [ ] Health becomes healthy only after Mihomo and FastAPI are ready.
- [ ] TERM/stop ends both processes without orphaning, and killing Mihomo makes
      `/health` fail immediately and the supervised container exit rather than
      silently serving broken jobs.
- [ ] Only port 8000 is published and internal controller/proxy listeners are
      loopback-bound.
- [ ] The container runs non-root and writes only intended `/data`/temporary
      paths; data survives recreation.
- [ ] Fast and browser modes import/run inside the built image.
- [ ] Controlled URL and YAML fixtures exercise progress, table/raw output,
      cancel/action/export, cache reuse and option isolation.
- [ ] Completed/cancelled/error snapshots survive container recreation, retain
      at most `MAX_JOBS` entries, never store a subscription URL/token/raw YAML,
      and can be viewed read-only or deleted from the Web page.
- [ ] Documentation exactly matches commands, environment/config defaults,
      limitations, privacy behavior, and upgrade/rollback steps.

## Dependencies

Requires completed `07-27-docker-core-hardening` and
`07-27-docker-result-workspace`.

## Out of Scope

- Publishing an image or pushing Git branches/releases.
- Kubernetes/Swarm manifests or external state services.
