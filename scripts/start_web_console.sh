#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

export GATEWAY_SN="${GATEWAY_SN:-$(gateway_sn)}"
export MQTT_HOST="${MQTT_HOST:-$(mqtt_host)}"
export MQTT_PORT="${MQTT_PORT:-$(mqtt_port)}"
export WEB_HOST="${WEB_HOST:-0.0.0.0}"
export WEB_PORT="${WEB_PORT:-8090}"
export MAVLINK_URL="${MAVLINK_URL:-udpout:127.0.0.1:$(mavlink_port)}"
export ARDUPILOT_DIR="${ARDUPILOT_DIR:-${HOME}/lxz/ardupilot}"
export SITL_PYTHON="${SITL_PYTHON:-/usr/bin/python3}"

echo "[INFO] Web observer console"
echo "[INFO] URL: http://${WEB_HOST}:${WEB_PORT}"
echo "[INFO] MQTT: ${MQTT_HOST}:${MQTT_PORT}, sn=${GATEWAY_SN}"
echo "[INFO] MAVLink URL: ${MAVLINK_URL}"

cd "${PROJECT_ROOT}"
exec "${PROJECT_PYTHON}" "${PROJECT_ROOT}/web/app.py"
