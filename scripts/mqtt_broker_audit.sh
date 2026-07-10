#!/usr/bin/env bash

# 临时开启 Mosquitto 全量审计，用于定位同一 topic 的重复发布者。
# 使用独立配置文件，disable 后会完全移除，不修改主配置或业务 ACL。
set -Eeuo pipefail

ACTION="${1:-status}"
AUDIT_CONFIG="${AUDIT_CONFIG:-/etc/mosquitto/conf.d/99-edge-gateway-audit.conf}"
BROKER_SERVICE="${BROKER_SERVICE:-mosquitto}"
BROKER_LOG="${BROKER_LOG:-/var/log/mosquitto/mosquitto.log}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[错误] 请通过 sudo 执行：sudo bash scripts/mqtt_broker_audit.sh <enable|disable|status>" >&2
  exit 1
fi

restart_broker() {
  systemctl restart "${BROKER_SERVICE}"
  systemctl is-active --quiet "${BROKER_SERVICE}"
}

case "${ACTION}" in
  enable)
    install -d -m 755 "$(dirname "${AUDIT_CONFIG}")"
    printf '%s\n' \
      '# Temporary Edge Gateway MQTT publisher audit.' \
      '# Remove through: mqtt_broker_audit.sh disable' \
      'log_type all' \
      'connection_messages true' \
      > "${AUDIT_CONFIG}"
    restart_broker
    echo "[成功] 已开启 MQTT 发布审计：${AUDIT_CONFIG}"
    echo "[信息] 运行一次严格 properties 测试后，使用："
    echo "       sudo grep -E 'Received PUBLISH|property/set_reply' ${BROKER_LOG} | tail -n 120"
    ;;
  disable)
    rm -f "${AUDIT_CONFIG}"
    restart_broker
    echo "[成功] 已关闭 MQTT 发布审计并重启 broker"
    ;;
  status)
    if [[ -f "${AUDIT_CONFIG}" ]]; then
      echo "[信息] MQTT 发布审计已开启：${AUDIT_CONFIG}"
    else
      echo "[信息] MQTT 发布审计未开启"
    fi
    systemctl --no-pager --full status "${BROKER_SERVICE}" | sed -n '1,12p'
    ;;
  *)
    echo "用法：$0 <enable|disable|status>" >&2
    exit 2
    ;;
esac
