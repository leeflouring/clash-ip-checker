# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.10-slim-bookworm
FROM --platform=$BUILDPLATFORM ${PYTHON_IMAGE} AS mihomo-downloader
ARG TARGETARCH
ARG MIHOMO_VERSION=1.19.18
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gzip && rm -rf /var/lib/apt/lists/*
RUN set -eux; case "$TARGETARCH" in amd64) asset="mihomo-linux-amd64-v${MIHOMO_VERSION}.gz"; sha="bbad6c9fa6322d870e94aab34b54097ffc880829be8fd804de79543d87f1f8e5" ;; arm64) asset="mihomo-linux-arm64-v${MIHOMO_VERSION}.gz"; sha="212e7a76c8a70951e329c3816f7a2076a979789366f327fbd121b3b8707755fb" ;; *) exit 1 ;; esac; curl -fSL --retry 5 -o /tmp/mihomo.gz "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/${asset}"; echo "$sha  /tmp/mihomo.gz" | sha256sum -c -; gunzip /tmp/mihomo.gz; install -m 0755 /tmp/mihomo /usr/local/bin/clash; rm /tmp/mihomo

FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data/jobs MIHOMO_HOME=/data/mihomo HOME=/home/app PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY --from=mihomo-downloader /usr/local/bin/clash /usr/local/bin/clash
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-liberation \
        fonts-wqy-zenhei \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libudev1 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxkbcommon0 \
        libxrandr2 \
    && playwright install --only-shell chromium \
    && rm -rf /ms-playwright/ffmpeg-* /var/lib/apt/lists/* /root/.cache
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app && mkdir -p /data/jobs /data/mihomo && chown -R app:app /app /data /home/app
COPY . .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh && chown app:app entrypoint.sh config.yaml
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
USER app
ENTRYPOINT ["/app/entrypoint.sh"]
