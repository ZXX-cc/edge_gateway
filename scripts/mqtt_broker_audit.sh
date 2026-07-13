#!/usr/bin/env bash

# 临时开启 Mosquitto 全量审计，用于定位同一 topic 的重复发布者。
# 使用独立配置文件，disable 后会完全移除，不修改主配置或业务 ACL。
set -Eeuo pipefail

ACTION="${1:-status}"
CLIENT_ID="${2:-}"
AUDIT_CONFIG="${AUDIT_CONFIG:-/etc/mosquitto/conf.d/99-edge-gateway-audit.conf}"
BROKER_SERVICE="${BROKER_SERVICE:-mosquitto}"
BROKER_LOG="${BROKER_LOG:-/var/log/mosquitto/mosquitto.log}"
BROKER_PORT="${BROKER_PORT:-1883}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[错误] 请通过 sudo 执行：sudo bash scripts/mqtt_broker_audit.sh <enable|disable|status|diagnose|client> [client_id]" >&2
  exit 1
fi

restart_broker() {
  systemctl restart "${BROKER_SERVICE}" && systemctl is-active --quiet "${BROKER_SERVICE}"
}

broker_listener_exists() {
  ss -ltnp 2>/dev/null | grep -qE ":${BROKER_PORT}([[:space:]]|$)"
}

diagnose_broker() {
  echo "[诊断] systemd 服务状态："
  systemctl --no-pager --full status "${BROKER_SERVICE}" 2>&1 | sed -n '1,24p' || true
  echo "[诊断] ${BROKER_PORT} 端口监听者："
  ss -ltnp 2>&1 | grep -E ":${BROKER_PORT}([[:space:]]|$)" || true
  echo "[诊断] MQTT broker 相关进程："
  pgrep -af 'mosquitto|nanomq|emqx' || true
  echo "[诊断] 最近的 systemd 日志："
  journalctl -u "${BROKER_SERVICE}" -n 50 --no-pager 2>&1 || true
}

case "${ACTION}" in
  enable)
    if ! systemctl is-active --quiet "${BROKER_SERVICE}" && broker_listener_exists; then
      echo "[错误] ${BROKER_SERVICE}.service 未运行，但端口 ${BROKER_PORT} 已被另一 broker 实例占用。" >&2
      echo "[信息] 为避免重启错误实例，本次未修改审计配置。" >&2
      diagnose_broker
      exit 3
    fi

    backup_config="$(mktemp)"
    had_config=0
    if [[ -f "${AUDIT_CONFIG}" ]]; then
      cp -a "${AUDIT_CONFIG}" "${backup_config}"
      had_config=1
    fi
    install -d -m 755 "$(dirname "${AUDIT_CONFIG}")"
    temporary_config="$(mktemp)"
    printf '%s\n' \
      '# Temporary Edge Gateway MQTT publisher audit.' \
      '# Remove through: mqtt_broker_audit.sh disable' \
      'log_type all' \
      'connection_messages true' \
      > "${temporary_config}"
    install -m 644 "${temporary_config}" "${AUDIT_CONFIG}"
    rm -f "${temporary_config}"

    if ! restart_broker; then
      echo "[错误] 开启审计后 ${BROKER_SERVICE}.service 重启失败，正在回滚配置。" >&2
      if [[ "${had_config}" -eq 1 ]]; then
        cp -a "${backup_config}" "${AUDIT_CONFIG}"
      else
        rm -f "${AUDIT_CONFIG}"
      fi
      rm -f "${backup_config}"
      systemctl restart "${BROKER_SERVICE}" >/dev/null 2>&1 || true
      diagnose_broker
      exit 3
    fi
    rm -f "${backup_config}"
    echo "[成功] 已开启 MQTT 发布审计：${AUDIT_CONFIG}"
    echo "[信息] 运行一次严格 properties 测试后，使用："
    echo "       sudo grep -E 'Received PUBLISH|property/set_reply' ${BROKER_LOG} | tail -n 120"
    ;;
  disable)
    rm -f "${AUDIT_CONFIG}"
    if ! restart_broker; then
      echo "[错误] 审计配置已移除，但 ${BROKER_SERVICE}.service 仍无法启动。" >&2
      echo "[信息] 通常表示端口 ${BROKER_PORT} 被手工启动、Docker 或另一 broker 占用。" >&2
      diagnose_broker
      exit 3
    fi
    echo "[成功] 已关闭 MQTT 发布审计并重启 broker"
    ;;
  status)
    if [[ -f "${AUDIT_CONFIG}" ]]; then
      echo "[信息] MQTT 发布审计已开启：${AUDIT_CONFIG}"
    else
      echo "[信息] MQTT 发布审计未开启"
    fi
    systemctl --no-pager --full status "${BROKER_SERVICE}" 2>&1 | sed -n '1,12p' || true
    echo "[信息] ${BROKER_PORT} 端口监听者："
    ss -ltnp 2>&1 | grep -E ":${BROKER_PORT}([[:space:]]|$)" || true
    ;;
  diagnose)
    diagnose_broker
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
    echo "[信息] 当前 ${BROKER_PORT} TCP 连接（本机连接会显示 PID，外部连接仅显示对端地址）："
    ss -tnp | grep ":${BROKER_PORT}" || true
    echo "[提示] 若日志显示该 client 从本机 IP:port 连接，可执行："
    echo "       sudo lsof -nP -iTCP:<port> -sTCP:ESTABLISHED"
    echo "[提示] 若对端为其他主机，请在该主机停止对应旧模拟器；不要关闭当前 edge_gateway 实例。"
    ;;
  *)
    echo "用法：$0 <enable|disable|status|diagnose|client> [client_id]" >&2
    exit 2
    ;;
esac
