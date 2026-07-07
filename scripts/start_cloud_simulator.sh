#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

SN="${GATEWAY_SN:-$(gateway_sn)}"
MQTT_HOST="${MQTT_HOST:-$(mqtt_host)}"
MQTT_PORT="${MQTT_PORT:-$(mqtt_port)}"
BIND_STATUS="${BIND_STATUS:-bound}"
MAX_REQUESTS="${MAX_REQUESTS:-0}"
TIMEOUT="${TIMEOUT:-120}"
ORGANIZATION_ID="${ORGANIZATION_ID:-org_sim_001}"
ORGANIZATION_NAME="${ORGANIZATION_NAME:-模拟组织}"
DEVICE_CALLSIGN="${DEVICE_CALLSIGN:-${SN}}"
ENABLE_STATUS="${ENABLE_STATUS:-1}"

ARGS=(
  "${PROJECT_ROOT}/tools/mqtt_cloud_simulator.py"
  --host "${MQTT_HOST}"
  --port "${MQTT_PORT}"
  --sn "${SN}"
  --bind-status "${BIND_STATUS}"
  --max-requests "${MAX_REQUESTS}"
  --timeout "${TIMEOUT}"
  --organization-id "${ORGANIZATION_ID}"
  --organization-name "${ORGANIZATION_NAME}"
  --device-callsign "${DEVICE_CALLSIGN}"
)

if [[ "${ENABLE_STATUS}" == "1" || "${ENABLE_STATUS}" == "true" ]]; then
  ARGS+=(--enable-status)
fi

echo "[INFO] Cloud API simulator"
echo "[INFO] host=${MQTT_HOST}, port=${MQTT_PORT}, sn=${SN}, bind_status=${BIND_STATUS}, enable_status=${ENABLE_STATUS}"
echo "[INFO] max_requests=${MAX_REQUESTS}; 0 means run forever"

cd "${PROJECT_ROOT}"
exec "${PROJECT_PYTHON}" "${ARGS[@]}"
