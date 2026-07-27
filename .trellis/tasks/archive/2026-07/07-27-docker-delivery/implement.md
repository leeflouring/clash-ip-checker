# Implementation Plan

1. Confirm both prerequisite children and full Python checks are green.
2. Pin runtime dependencies from a successful resolved environment; research
   and record official Mihomo asset/checksum naming before changing Dockerfile.
3. Add/update `.dockerignore`, Dockerfile, entrypoint, Compose, health route and
   authoritative example configuration.
4. Update Chinese/English README, changelog, deployment and troubleshooting
   docs to final behavior.
5. Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall main.py core
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl.exe -fsS http://127.0.0.1:8000/health
curl.exe -fsS http://127.0.0.1:8000/ipcheck
docker compose logs --tail=200 clash-checker
```

6. Exercise controlled URL/YAML jobs, result table/raw YAML, cancellation,
   edit/delete/recheck, selective YAML, CSV, cache reuse and differing options.
7. Exercise terminal history persistence, restart reload, read-only table
   viewing, deletion, API-token protection and record-count retention.
8. Verify non-root UID, intended listeners, volume persistence, Mihomo failure,
   `/health` failure plus container exit after killing Mihomo, and graceful
   stop; save evidence under this task.
9. Run full parent-scope review before proposing commits.

## Risk / Rollback

Dockerfile, dependency pins, and entrypoint are the rollback boundary. Preserve
the previous compose data volume/input subscription; rolling back the image may
discard only regenerable checked-cache files.
