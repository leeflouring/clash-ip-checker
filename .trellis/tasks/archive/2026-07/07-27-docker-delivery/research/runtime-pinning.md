# Runtime pinning research (2026-07-27)

status: complete

## Mihomo release and integrity

Use the existing official release `v1.19.18` from MetaCubeX (queried 2026-07-27):

- amd64 generic asset: `mihomo-linux-amd64-v1.19.18.gz`; GitHub API published digest `sha256:bbad6c9fa6322d870e94aab34b54097ffc880829be8fd804de79543d87f1f8e5`.
- arm64 asset: `mihomo-linux-arm64-v1.19.18.gz`; digest `sha256:212e7a76c8a70951e329c3816f7a2076a979789366f327fbd121b3b8707755fb`.

Official release page: https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.18
Official API query used: `https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/v1.19.18` (the `assets[].name`, `assets[].digest`, and `browser_download_url` fields). Dockerfile can map `TARGETARCH=amd64|arm64` to those exact names and verify the compressed download with `sha256sum -c` (or compare `sha256sum` output) before `gunzip`. No separate checksum-file asset was exposed by the API; use the API-published per-asset digest rather than inventing a checksum file URL.

## Playwright Chromium

The official Playwright Python CLI command is `playwright install chromium`; official Python source/docs: https://github.com/microsoft/playwright-python/blob/main/CLAUDE.md and https://github.com/microsoft/playwright-python/blob/main/playwright/__main__.py (queried 2026-07-27). The upstream Docker build uses `playwright install --with-deps` to install browser plus OS libraries (example: https://github.com/microsoft/playwright-python/blob/main/utils/docker/Dockerfile.noble). On `python:3.10-slim-bookworm`, run `playwright install --with-deps chromium` as root during build, or install Debian dependencies separately then run `playwright install chromium`; `--with-deps` invokes apt and requires root/noninteractive apt handling. Install after pinning the `playwright` package so browser revision matches the package.

## Python dependency pins

The current local successful environment (`python -m pip freeze`, queried 2026-07-27) resolves the direct requirements to:

```
aiohttp==3.14.1
PyYAML==6.0.3
curl_cffi==0.15.0
fastapi==0.136.3
uvicorn==0.49.0
python-multipart==0.0.32
playwright==1.60.0
```

These are the minimum direct pins; do not add the many unrelated packages present in the developer environment. Transitive packages remain pip-resolved by these top-level pins.

## Gaps

The Mihomo GitHub API supplies digests inline per asset but no standalone `checksums.txt` asset for v1.19.18. If the build requires a downloadable checksum file specifically, that is blocked; otherwise inline API digests above are authoritative and directly usable in Dockerfile verification.
