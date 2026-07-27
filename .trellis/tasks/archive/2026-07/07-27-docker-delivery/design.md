# Technical Design

## Image

- Keep one Debian slim Python image unless measured build/runtime evidence
  requires a multistage split.
- Map Docker `TARGETARCH` (`amd64`, `arm64`) to Mihomo release asset names.
- Pin Python versions in `requirements.txt`; pin Mihomo version and verify its
  published checksum before installation.
- Install Playwright Chromium and required system libraries during build.
- Create an unprivileged app user and own `/app` plus `/data`.

## Runtime

- Generate Mihomo config under `/data/mihomo` with loopback controller and
  mixed-port. No DNS listener.
- Strict Bash entrypoint starts Mihomo, waits with a hard deadline, starts
  Uvicorn, traps TERM/INT, terminates/reaps both children, and exits if either
  dies.
- `/health` probes Mihomo on every request and returns failure when it is
  unreachable; the supervisor exits the container if Mihomo dies after startup.
- Compose publishes `8000`, mounts a named `/data` volume, defines healthcheck,
  and does not expose internal listeners.

## Configuration

Use documented environment variables with one authoritative default path.
Security-sensitive opt-ins (`ALLOW_PRIVATE_SUBSCRIPTIONS`, `API_TOKEN`) are
explicit and disabled/empty by default. When configured, the token protects all
job/result/subscription routes via Bearer header or masked compatibility query;
health/static UI stay public.

## History

- Keep a small atomic JSON history file at `/data/jobs/history.json`; reuse
  `MAX_JOBS` as the retention count and add no database/dependency.
- Persist only terminal public snapshots: opaque job ID, masked label, times,
  status/statistics and already-sanitized node result rows. Never persist the
  subscription URL, API token, raw YAML, source document or proxy credentials.
- Expose protected list/get/delete routes. Historical snapshots are read-only
  in the Web table; filters and sorting still work, while mutation, raw YAML and
  export actions remain unavailable.

## Documentation

Lead with Compose quick start, then URL conversion, Web workspace, table/CSV vs
Clash YAML, browser-mode cost, configuration/security, persistence, upgrade,
troubleshooting, and rollback. Keep Chinese and English feature lists aligned.

## Verification

Use controlled local fixture data; do not rely on a private real subscription
or external purity service for deterministic gates. One optional live smoke can
be recorded separately when network is available.
