#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

NEST_HOST="${NEST_HOST:-0.0.0.0}"
NEST_PORT="${NEST_PORT:-$(nest_port)}"
DRONE_HOST="${DRONE_HOST:-$(nest_sitl_host)}"
DRONE_PORT="${DRONE_PORT:-0}"
NEST_STATUS_LOG="${NEST_STATUS_LOG:-logs/nest_simulator/nest_status.log}"
DRONE_STATUS_LOG="${DRONE_STATUS_LOG:-logs/nest_simulator/drone_status.log}"
MISSION_FILE="${MISSION_FILE:-/home/fenghuolun/zwz/HD.kml}"
MISSION_UPLOAD_TIMEOUT="${MISSION_UPLOAD_TIMEOUT:-30}"

echo "[INFO] MAVLink nest simulator"
echo "[INFO] bind=${NEST_HOST}:${NEST_PORT}"
echo "[INFO] SITL proxy bind=${DRONE_HOST}:${DRONE_PORT} (legacy only; set DRONE_PORT to enable)"
echo "[INFO] mission_file=${MISSION_FILE}"
echo "[INFO] nest_status_log=${NEST_STATUS_LOG}"
echo "[INFO] drone_status_log=${DRONE_STATUS_LOG}"
echo "[INFO] NEST_HOST is the simulator bind address; gateway nest_mavlink.connection.host must be a reachable target such as 127.0.0.1 or the simulator host IP, not 0.0.0.0"
echo "[INFO] port flow: gateway <-> nest simulator on ${NEST_PORT}; SITL/flight-controller connects to gateway mavlink port directly"

cd "${PROJECT_ROOT}"
if [[ "${DRONE_PORT}" == "0" ]]; then
  exec "${PROJECT_PYTHON}" "${PROJECT_ROOT}/src/nest_simulator.py" \
    --host "${NEST_HOST}" \
    --port "${NEST_PORT}" \
    --nest-status-log "${NEST_STATUS_LOG}" \
    --mission-file "${MISSION_FILE}" \
    --mission-upload-timeout "${MISSION_UPLOAD_TIMEOUT}"
fi

exec "${PROJECT_PYTHON}" "${PROJECT_ROOT}/src/nest_simulator.py" \
  --host "${NEST_HOST}" \
  --port "${NEST_PORT}" \
  --nest-status-log "${NEST_STATUS_LOG}" \
  --drone-host "${DRONE_HOST}" \
  --drone-port "${DRONE_PORT}" \
  --mission-file "${MISSION_FILE}" \
  --mission-upload-timeout "${MISSION_UPLOAD_TIMEOUT}" \
  --drone-status-log "${DRONE_STATUS_LOG}"
