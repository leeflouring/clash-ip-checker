#!/bin/bash
set -Eeuo pipefail

mkdir -p "$DATA_DIR" "$MIHOMO_HOME"
cat > "$MIHOMO_HOME/config.yaml" <<'EOF'
log-level: error
mode: global
allow-lan: false
bind-address: 127.0.0.1
mixed-port: 7890
external-controller: 127.0.0.1:9090
ipv6: false
EOF

mihomo_pid=0
uvicorn_pid=0
signal_status=0

on_signal() {
  signal_status="$1"
}

stop_children() {
  trap - TERM INT
  for pid in "$uvicorn_pid" "$mihomo_pid"; do
    if (( pid > 0 )) && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done

  local deadline=$((SECONDS + 10))
  while (( SECONDS < deadline )); do
    local running=0
    for pid in "$uvicorn_pid" "$mihomo_pid"; do
      if (( pid > 0 )) && kill -0 "$pid" 2>/dev/null; then
        running=1
      fi
    done
    (( running == 0 )) && break
    sleep 0.2
  done

  for pid in "$uvicorn_pid" "$mihomo_pid"; do
    if (( pid > 0 )) && kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    if (( pid > 0 )); then
      wait "$pid" 2>/dev/null || true
    fi
  done
}

trap 'on_signal 143' TERM
trap 'on_signal 130' INT

export SAFE_PATHS="${SAFE_PATHS:+${SAFE_PATHS}:}${DATA_DIR}"
clash -d "$MIHOMO_HOME" &
mihomo_pid=$!

ready=0
deadline=$((SECONDS + 15))
while (( SECONDS < deadline && signal_status == 0 )); do
  if ! kill -0 "$mihomo_pid" 2>/dev/null; then
    echo "Mihomo exited before readiness" >&2
    stop_children
    exit 1
  fi
  if python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:9090/version", timeout=1)' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if (( signal_status != 0 )); then
  stop_children
  exit "$signal_status"
fi
if (( ready == 0 )); then
  echo "Mihomo readiness timed out" >&2
  stop_children
  exit 1
fi

uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" &
uvicorn_pid=$!

set +e
wait -n "$mihomo_pid" "$uvicorn_pid"
child_status=$?
set -e

exit_status="$child_status"
if (( signal_status != 0 )); then
  exit_status="$signal_status"
elif (( child_status == 0 )); then
  # ponytail: either service exiting cleanly is still unexpected for a server.
  exit_status=1
fi

stop_children
exit "$exit_status"
