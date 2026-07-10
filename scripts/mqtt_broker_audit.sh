#!/usr/bin/env bash

# 临时开启 Mosquitto 全量审计，用于定位同一 topic 的重复发布者。
# 使用独立配置文件，disable 后会完全移除，不修改主配置或业务 ACL。
set -Eeuo pipefail

ACTION="${1:-status}"
CLIENT_ID="${2:-}"
AUDIT_CONFIG="${AUDIT_CONFIG:-/etc/mosquitto/conf.d/99-edge-gateway-audit.conf}"
BROKER_SERVICE="${BROKER_SERVICE:-mosquitto}"
BROKER_LOG="${BROKER_LOG:-/var/log/mosquitto/mosquitto.log}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[错误] 请通过 sudo 执行：sudo bash scripts/mqtt_broker_audit.sh <enable|disable|status|client> [client_id]" >&2
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
  client)
    if [[ -z "${CLIENT_ID}" ]]; then
      echo "[错误] client 模式需要 MQTT client_id" >&2
      echo "用法：$0 client <client_id>" >&2
      exit 2
    fi
    echo "[信息] 查询 client_id：${CLIENT_ID}"
    echo "[信息] broker 日志中的连接与发布记录（最近 160 行上下文）："
    grep -n -F -C 3 "${CLIENT_ID}" "${BROKER_LOG}" | tail -n 160 || true
    echo "[信息] 当前 1883 TCP 连接（本机连接会显示 PID，外部连接仅显示对端地址）："
    ss -tnp | grep ':1883' || true
    echo "[提示] 若日志显示该 client 从本机 IP:port 连接，可执行："
    echo "       sudo lsof -nP -iTCP:<port> -sTCP:ESTABLISHED"
    echo "[提示] 若对端为其他主机，请在该主机停止对应旧模拟器；不要关闭当前 edge_gateway 实例。"
    ;;
  *)
    echo "用法：$0 <enable|disable|status|client> [client_id]" >&2
    exit 2
    ;;
esac
