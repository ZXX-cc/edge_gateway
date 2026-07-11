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

declare -a COMPONENT_SUMMARY=()
COMPONENT_FAILURES=0

component_enabled() {
  local value="$1"
  [[ "${value}" == "1" || "${value}" == "true" ]]
}

record_component() {
  local name="$1"
  local status="$2"
  local detail="$3"
  COMPONENT_SUMMARY+=("${name}|${status}|${detail}")
  if [[ "${status}" == "失败" ]]; then
    COMPONENT_FAILURES=$((COMPONENT_FAILURES + 1))
  fi
}

print_component_summary() {
  echo
  echo "[汇总] 外围组件启动结果:"
  local item name status detail
  for item in "${COMPONENT_SUMMARY[@]}"; do
    IFS='|' read -r name status detail <<<"${item}"
    printf '[汇总] %-18s %-8s %s\n' "${name}" "${status}" "${detail}"
  done
}

print_failure_tail() {
  local log_file="$1"
  if [[ -f "${log_file}" ]]; then
    echo "[失败原因] 最近日志: ${log_file}"
    tail -n 40 "${log_file}" || true
  else
    echo "[失败原因] 日志文件不存在: ${log_file}" >&2
  fi
}

start_foreground_component() {
  local name="$1"
  shift

  echo "[信息] 正在启动 ${name}..."
  local rc=0
  "$@" || rc=$?
  if [[ "${rc}" -eq 0 ]]; then
    echo "[成功] ${name} 已启动/检查通过"
    record_component "${name}" "已启动" "前台检查通过"
    return 0
  fi

  echo "[失败] ${name} 启动失败，退出码=${rc}" >&2
  record_component "${name}" "失败" "退出码=${rc}；查看上方诊断输出"
  return "${rc}"
}

start_background() {
  local name="$1"
  local display_name="$2"
  shift 2
  local startup_wait="${STARTUP_WAIT_SECONDS:-1}"
  local log_file="${LOG_DIR}/${name}.log"

  echo "[信息] 正在后台启动 ${display_name}；日志=${log_file}"
  "$@" >"${log_file}" 2>&1 &
  local pid=$!
  printf '%s\n' "${pid}" >"${PID_DIR}/${name}.pid"
  sleep "${startup_wait}"

  if kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[成功] ${display_name} 已启动，pid=${pid}"
    record_component "${display_name}" "已启动" "pid=${pid}；日志=${log_file}"
    return 0
  fi

  local rc=1
  wait "${pid}" || rc=$?
  rm -f "${PID_DIR}/${name}.pid"
  echo "[失败] ${display_name} 启动后立即退出，退出码=${rc}" >&2
  print_failure_tail "${log_file}"
  record_component "${display_name}" "失败" "退出码=${rc}；日志=${log_file}"
  return "${rc}"
}

skip_component() {
  local name="$1"
  local reason="$2"
  echo "[跳过] ${name}：${reason}"
  record_component "${name}" "跳过" "${reason}"
}

echo "[信息] 外围组件日志目录: ${LOG_DIR}"
echo "[信息] 本脚本不会启动 Edge Gateway 网关主进程。"
echo "[信息] 本脚本不会管理 Docker 飞控/云平台。"

BROKER_READY=1
if component_enabled "${START_BROKER}"; then
  if ! start_foreground_component "MQTT Broker" "${SCRIPT_DIR}/start_mqtt_broker.sh"; then
    BROKER_READY=0
  fi
else
  skip_component "MQTT Broker" "START_BROKER=${START_BROKER}"
fi

if component_enabled "${START_CLOUD}"; then
  if [[ "${BROKER_READY}" == "1" ]]; then
    start_background "cloud_simulator" "云端模拟器" "${SCRIPT_DIR}/start_cloud_simulator.sh" || true
  else
    skip_component "云端模拟器" "依赖 MQTT Broker，当前 broker 未就绪"
  fi
else
  skip_component "云端模拟器" "START_CLOUD=${START_CLOUD}"
fi

if component_enabled "${START_OBSERVER}"; then
  if [[ "${BROKER_READY}" == "1" ]]; then
    start_background "mqtt_observer" "MQTT 观察端" "${SCRIPT_DIR}/start_mqtt_observer.sh" || true
  else
    skip_component "MQTT 观察端" "依赖 MQTT Broker，当前 broker 未就绪"
  fi
else
  skip_component "MQTT 观察端" "START_OBSERVER=${START_OBSERVER}"
fi

if component_enabled "${START_WEB}"; then
  start_background "web_console" "Web 观察台" "${SCRIPT_DIR}/start_web_console.sh" || true
else
  skip_component "Web 观察台" "START_WEB=${START_WEB}"
fi

if component_enabled "${START_NEST}"; then
  start_background "nest_simulator" "机巢模拟器" "${SCRIPT_DIR}/start_nest_simulator.sh" || true
else
  skip_component "机巢模拟器" "START_NEST=${START_NEST}"
fi

if component_enabled "${START_SITL}"; then
  start_background "arduplane_sitl" "ArduPlane QuadPlane SITL" "${SCRIPT_DIR}/start_arduplane_sitl.sh" || true
else
  skip_component "ArduPlane QuadPlane SITL" "START_SITL=${START_SITL}"
fi

print_component_summary

echo "[信息] PID 文件目录: ${PID_DIR}"
echo "[信息] 日志目录: ${LOG_DIR}"
echo "[信息] 停止命令: ${SCRIPT_DIR}/stop_support_stack.sh"

if [[ "${BROKER_READY}" == "0" ]]; then
  echo "[失败] MQTT Broker 未就绪，依赖 MQTT 的组件已跳过。请先修复上方 broker 失败原因。" >&2
  exit 1
fi

if [[ "${COMPONENT_FAILURES}" -gt 0 ]]; then
  echo "[失败] 有 ${COMPONENT_FAILURES} 个外围组件启动失败，请查看上方汇总和对应日志。" >&2
  exit 1
fi

echo "[成功] 外围组件启动命令已执行完毕"
