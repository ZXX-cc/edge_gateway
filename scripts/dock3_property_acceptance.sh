#!/usr/bin/env bash

# Dock 3 official 49-property acceptance for simulator-only deployments.
# All logs and the JSON summary are stored in one timestamped evidence folder.
# Extra arguments are forwarded to mqtt_dock3_property_coverage_test.py.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
EVIDENCE_DIR="${DOCK3_PROPERTY_EVIDENCE_DIR:-${PROJECT_ROOT}/logs/acceptance_evidence/${TIMESTAMP}_dock3_properties}"
MQTT_HOST="${DOCK3_MQTT_HOST:-192.168.2.188}"
MQTT_PORT="${DOCK3_MQTT_PORT:-1883}"

mkdir -p "${EVIDENCE_DIR}"
export TEST_LOG_DIR="${EVIDENCE_DIR}"

cd "${PROJECT_ROOT}"

echo "[信息] Dock 3 属性验收 MQTT: ${MQTT_HOST}:${MQTT_PORT}"

bash scripts/run_test_with_log.sh --name dock3-property-unit -- \
  python -m unittest -v \
    tests.test_dock3_properties \
    tests.test_device_manager_dock3_osd \
    tests.test_dock3_property_tool_entrypoint \
    tests.test_dock3_osd_state_client \
    tests.test_mavlink_telemetry_mapping

bash scripts/run_test_with_log.sh --name dock3-property-mqtt -- \
  python tools/mqtt_dock3_property_coverage_test.py \
    --host "${MQTT_HOST}" \
    --port "${MQTT_PORT}" \
    --evidence-dir "${EVIDENCE_DIR}" \
    "$@"

echo "[成功] Dock 3 属性模拟验收完成"
echo "[证据目录] ${EVIDENCE_DIR}"
echo "[复制结果] sed -n '1,260p' ${EVIDENCE_DIR}/*.log"
echo "[JSON摘要] ${EVIDENCE_DIR}/dock3_property_coverage_summary.json"
