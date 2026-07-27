# Core Layer

## Pre-Development Checklist

- For Docker subscription, job, cache, result, or SSE changes, read
  `docker-job-contract.md`.
- For image, Compose, entrypoint, health, browser runtime, or deployment
  changes, read `docker-runtime-contract.md`.
- Trace every caller of `JobManager` and `CheckerService` before changing their
  signatures.

## Quality Check

- Run `python -m unittest discover -s tests -v`.
- Run `python -m compileall main.py core tests`.
- Verify subscription URLs are never stored or logged in plaintext by the job
  layer.
- Verify late callbacks cannot mutate terminal jobs and output identity includes
  every transformation option.
- Build the image and verify `/health`, the non-root UID, read-only rootfs,
  loopback-only Mihomo listeners, and Chromium headless-shell launch.
