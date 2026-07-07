#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

START_BROKER="${START_BROKER:-1}"
START_CLOUD="${START_CLOUD:-1}"
START_OBSERVER="${START_OBSERVER:-1}"
START_WEB="${START_WEB:-0}"
START_NEST="${START_NEST:-1}"
START_SITL="${START_SITL:-0}"

LOG_DIR="${SUPPORT_LOG_DIR:-$(support_log_dir)}"
PID_DIR="$(pid_dir)"

start_background() {
  local name="$1"
  shift
  local log_file="${LOG_DIR}/${name}.log"

  echo "[INFO] starting ${name}; log=${log_file}"
  "$@" >"${log_file}" 2>&1 &
  local pid=$!
  printf '%s\n' "${pid}" >"${PID_DIR}/${name}.pid"
  echo "[OK] ${name} pid=${pid}"
}

echo "[INFO] support stack log dir: ${LOG_DIR}"
echo "[INFO] Edge Gateway is intentionally NOT started by this script."
echo "[INFO] Docker flight/cloud platform is intentionally NOT managed by this script."

if [[ "${START_BROKER}" == "1" || "${START_BROKER}" == "true" ]]; then
  "${SCRIPT_DIR}/start_mqtt_broker.sh"
fi

if [[ "${START_CLOUD}" == "1" || "${START_CLOUD}" == "true" ]]; then
  start_background "cloud_simulator" "${SCRIPT_DIR}/start_cloud_simulator.sh"
fi

if [[ "${START_OBSERVER}" == "1" || "${START_OBSERVER}" == "true" ]]; then
  start_background "mqtt_observer" "${SCRIPT_DIR}/start_mqtt_observer.sh"
fi

if [[ "${START_WEB}" == "1" || "${START_WEB}" == "true" ]]; then
  start_background "web_console" "${SCRIPT_DIR}/start_web_console.sh"
fi

if [[ "${START_NEST}" == "1" || "${START_NEST}" == "true" ]]; then
  start_background "nest_simulator" "${SCRIPT_DIR}/start_nest_simulator.sh"
fi

if [[ "${START_SITL}" == "1" || "${START_SITL}" == "true" ]]; then
  start_background "arducopter_sitl" "${SCRIPT_DIR}/start_arducopter_sitl.sh"
fi

echo "[OK] support stack commands issued"
echo "[INFO] pid files: ${PID_DIR}"
echo "[INFO] logs: ${LOG_DIR}"
echo "[INFO] stop with: ${SCRIPT_DIR}/stop_support_stack.sh"
