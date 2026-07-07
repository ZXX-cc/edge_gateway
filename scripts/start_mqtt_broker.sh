#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

BROKER_MODE="${BROKER_MODE:-systemd}"
BROKER_SERVICE="${BROKER_SERVICE:-mosquitto}"
BROKER_PORT="${BROKER_PORT:-$(mqtt_port)}"

echo "[INFO] MQTT broker mode: ${BROKER_MODE}"
echo "[INFO] expected broker port: ${BROKER_PORT}"

if [[ "${BROKER_MODE}" == "systemd" ]]; then
  require_command systemctl
  echo "[INFO] restarting ${BROKER_SERVICE}..."
  sudo systemctl restart "${BROKER_SERVICE}"
  sudo systemctl status "${BROKER_SERVICE}" --no-pager -l || true
elif [[ "${BROKER_MODE}" == "check" ]]; then
  echo "[INFO] check-only mode; not starting or restarting broker"
else
  echo "[ERROR] unsupported BROKER_MODE=${BROKER_MODE}; use systemd or check" >&2
  exit 2
fi

if command -v ss >/dev/null 2>&1; then
  echo "[INFO] listening sockets on :${BROKER_PORT}"
  ss -tlnp 2>/dev/null | grep -E ":${BROKER_PORT}\\b" || {
    echo "[WARN] no TCP listener found on port ${BROKER_PORT}" >&2
    exit 1
  }
fi

echo "[OK] MQTT broker is ready on port ${BROKER_PORT}"
