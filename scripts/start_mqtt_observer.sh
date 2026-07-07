#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_command mosquitto_sub

SN="${GATEWAY_SN:-$(gateway_sn)}"
MQTT_HOST="${MQTT_HOST:-$(mqtt_host)}"
MQTT_PORT="${MQTT_PORT:-$(mqtt_port)}"
OBSERVER_LOG_DIR="${OBSERVER_LOG_DIR:-${PROJECT_ROOT}/logs/mqtt_observer}"
mkdir -p "${OBSERVER_LOG_DIR}"
LOG_FILE="${OBSERVER_LOG_FILE:-${OBSERVER_LOG_DIR}/mqtt_observer_$(date +%Y%m%d_%H%M%S).log}"

AUTH_ARGS=()
if [[ -n "${MQTT_USERNAME:-}" ]]; then
  AUTH_ARGS=(-u "${MQTT_USERNAME}" -P "${MQTT_PASSWORD:-}")
fi

echo "[INFO] MQTT observer"
echo "[INFO] host=${MQTT_HOST}, port=${MQTT_PORT}, sn=${SN}"
echo "[INFO] topics: thing/product/${SN}/#, sys/product/${SN}/#"
echo "[INFO] log: ${LOG_FILE}"

mosquitto_sub \
  -h "${MQTT_HOST}" \
  -p "${MQTT_PORT}" \
  "${AUTH_ARGS[@]}" \
  -t "thing/product/${SN}/#" \
  -t "sys/product/${SN}/#" \
  -v | tee -a "${LOG_FILE}"
