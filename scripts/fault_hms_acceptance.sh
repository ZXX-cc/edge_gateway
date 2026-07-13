#!/usr/bin/env bash

# 一键生成“独立日志 + 数据保留 + 模拟故障注入”验收证据。
# 仅在 Ubuntu 联调服务器执行；运行前需启动 MQTT、机巢模拟器和网关。

set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}" || exit 1

timestamp="$(date '+%Y%m%d_%H%M%S')"
EVIDENCE_ROOT="${1:-${PROJECT_ROOT}/logs/acceptance_evidence/${timestamp}}"
FAULT_FILE="${FAULT_STATE_FILE:-/tmp/edge_gateway_nest_faults.json}"
mkdir -p "${EVIDENCE_ROOT}"
MAIN_LOG="${EVIDENCE_ROOT}/acceptance.log"

restore_faults() {
  mkdir -p "$(dirname "${FAULT_FILE}")"
  printf '%s\n' '{"active":false,"scenario":"acceptance-shell-final-recovery","faults":{}}' > "${FAULT_FILE}"
}
trap restore_faults EXIT INT TERM

run_step() {
  local name="$1"
  shift
  echo "[步骤] ${name}"
  "$@" 2>&1 | tee "${EVIDENCE_ROOT}/${name}.log"
  local rc=${PIPESTATUS[0]}
  if [[ ${rc} -ne 0 ]]; then
    echo "[失败] ${name}, exit=${rc}"
    return "${rc}"
  fi
  echo "[通过] ${name}"
}

{
  echo "[信息] 验收证据目录: ${EVIDENCE_ROOT}"
  echo "[信息] Git 提交: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "[信息] 开始时间: $(date --iso-8601=seconds 2>/dev/null || date)"

  run_step "00-fault-unit" \
    python -m unittest -v tests.test_fault_injection_support || exit $?

  run_step "01-independent-logs" \
    python tools/log_category_audit.py --output-dir "${EVIDENCE_ROOT}/independent_logs" || exit $?

  run_step "02-data-retention" \
    python tools/database_retention_audit.py \
      --config config/gateway.yaml \
      --database "${EVIDENCE_ROOT}/retention_audit.db" \
      --keep-database || exit $?

  run_step "03-fault-hms-mqtt" \
    python tools/mqtt_fault_injection_acceptance.py \
      --config config/gateway.yaml \
      --timeout 20 \
      --fault-state-file "${FAULT_FILE}" \
      --evidence-dir "${EVIDENCE_ROOT}/fault_injection" || exit $?

  mkdir -p "${EVIDENCE_ROOT}/actual_log_tails"
  for category in mqtt mavlink_raw mission alarm nest_plc flight_control payload; do
    latest="$(find "logs/${category}" -type f 2>/dev/null | sort | tail -n 1)"
    if [[ -n "${latest}" ]]; then
      tail -n 500 "${latest}" > "${EVIDENCE_ROOT}/actual_log_tails/${category}.log"
    fi
  done
  gateway_latest="$(find logs -maxdepth 2 -type f -name 'edge_gateway_*.log' 2>/dev/null | sort | tail -n 1)"
  if [[ -n "${gateway_latest}" ]]; then
    tail -n 500 "${gateway_latest}" > "${EVIDENCE_ROOT}/actual_log_tails/gateway.log"
  fi
  if [[ -f logs/nest_simulator/fault_injection.log ]]; then
    cp logs/nest_simulator/fault_injection.log "${EVIDENCE_ROOT}/actual_log_tails/fault_injection.jsonl"
  fi

  echo "[通过] 全部验收完成"
  echo "[信息] 结束时间: $(date --iso-8601=seconds 2>/dev/null || date)"
  echo "[信息] 证据目录: ${EVIDENCE_ROOT}"
  echo "[信息] 汇总文件: ${EVIDENCE_ROOT}/fault_injection/fault_injection_summary.json"
  echo "[信息] 完整日志: ${MAIN_LOG}"
} 2>&1 | tee "${MAIN_LOG}"
rc=${PIPESTATUS[0]}

find "${EVIDENCE_ROOT}" -type f \
  ! -name 'SHA256SUMS' ! -name 'acceptance.log' \
  -print0 | sort -z | xargs -0 sha256sum > "${EVIDENCE_ROOT}/SHA256SUMS"

exit "${rc}"
