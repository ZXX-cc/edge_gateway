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
EMQX_CONTAINER="${EMQX_CONTAINER:-}"
MQTT_CAPTURE_DIR="${MQTT_CAPTURE_DIR:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[错误] 请通过 sudo 执行：sudo bash scripts/mqtt_broker_audit.sh <enable|disable|status|diagnose|clients|client|capture-start|capture-stop|capture-status> [argument]" >&2
  exit 1
fi

restart_broker() {
  systemctl restart "${BROKER_SERVICE}" && systemctl is-active --quiet "${BROKER_SERVICE}"
}

broker_listener_exists() {
  ss -ltnp 2>/dev/null | grep -qE ":${BROKER_PORT}([[:space:]]|$)"
}

emqx_container() {
  local detected
  if [[ -n "${EMQX_CONTAINER}" ]]; then
    if docker inspect -f '{{.State.Running}}' "${EMQX_CONTAINER}" 2>/dev/null | grep -qx true; then
      printf '%s\n' "${EMQX_CONTAINER}"
      return 0
    fi
    echo "[错误] 指定的 EMQX_CONTAINER=${EMQX_CONTAINER} 不存在或未运行。" >&2
    return 0
  fi

  detected="$(
    docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null \
      | awk 'tolower($0) ~ /emqx/ {print $1; exit}' \
      || true
  )"
  if [[ -n "${detected}" ]]; then
    printf '%s\n' "${detected}"
    return 0
  fi
  return 0
}

emqx_ctl() {
  local container first_error second_error
  container="$1"
  shift

  if first_error="$(
    docker exec -u emqx "${container}" /opt/emqx/bin/emqx ctl "$@" 2>&1
  )"; then
    printf '%s\n' "${first_error}"
    return 0
  fi
  if second_error="$(
    docker exec "${container}" /opt/emqx/bin/emqx ctl "$@" 2>&1
  )"; then
    printf '%s\n' "${second_error}"
    return 0
  fi

  echo "[emqx 用户执行失败]" >&2
  printf '%s\n' "${first_error}" >&2
  echo "[root 用户回退失败]" >&2
  printf '%s\n' "${second_error}" >&2
  return 1
}

capture_dir() {
  local requested
  requested="${CLIENT_ID:-${MQTT_CAPTURE_DIR}}"
  if [[ -z "${requested}" ]]; then
    requested="logs/acceptance_evidence/$(date '+%Y%m%d_%H%M%S')_mqtt_publishers"
  fi
  readlink -m "${requested}"
}

capture_start() {
  local directory pid_file pcap log_file capture_pid
  if ! command -v tcpdump >/dev/null 2>&1; then
    echo "[错误] 未安装 tcpdump；请先执行 sudo apt-get install tcpdump。" >&2
    return 4
  fi
  directory="$(capture_dir)"
  pid_file="${directory}/tcpdump.pid"
  pcap="${directory}/mqtt-publishers.pcap"
  log_file="${directory}/tcpdump.log"
  mkdir -p "${directory}"
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    chown "${SUDO_USER}:$(id -gn "${SUDO_USER}")" "${directory}"
  fi
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
    echo "[错误] MQTT 抓包已在运行：PID=$(cat "${pid_file}")" >&2
    return 5
  fi
  rm -f "${pid_file}" "${pcap}" "${log_file}"
  nohup tcpdump -i any -nn -s 0 -U -w "${pcap}" \
    "tcp dst port ${BROKER_PORT}" >"${log_file}" 2>&1 &
  capture_pid=$!
  printf '%s\n' "${capture_pid}" > "${pid_file}"
  sleep 1
  if ! kill -0 "${capture_pid}" 2>/dev/null; then
    echo "[错误] tcpdump 启动失败：" >&2
    sed -n '1,80p' "${log_file}" >&2 || true
    rm -f "${pid_file}"
    return 6
  fi
  echo "[成功] MQTT 发布源抓包已启动：PID=${capture_pid}"
  echo "[证据目录] ${directory}"
}

capture_stop() {
  local directory pid_file pcap text_file matches_file summary_file summary_body capture_pid
  directory="$(capture_dir)"
  pid_file="${directory}/tcpdump.pid"
  pcap="${directory}/mqtt-publishers.pcap"
  text_file="${directory}/mqtt-publishers.txt"
  matches_file="${directory}/mqtt-publisher-matches.log"
  summary_file="${directory}/mqtt-publisher-summary.tsv"
  summary_body="${directory}/.mqtt-publisher-summary.body"
  if [[ ! -f "${pid_file}" ]]; then
    echo "[错误] 未找到抓包 PID 文件：${pid_file}" >&2
    return 7
  fi
  capture_pid="$(cat "${pid_file}")"
  if kill -0 "${capture_pid}" 2>/dev/null; then
    kill -INT "${capture_pid}"
    for _ in $(seq 1 30); do
      kill -0 "${capture_pid}" 2>/dev/null || break
      sleep 0.1
    done
  fi
  rm -f "${pid_file}"
  if [[ ! -s "${pcap}" ]]; then
    echo "[错误] 抓包文件为空或不存在：${pcap}" >&2
    return 8
  fi
  tcpdump -nn -tttt -A -r "${pcap}" > "${text_file}" 2>/dev/null
  grep -n -B 2 -A 8 -E \
    'EDGEGW001|DRONESIM001|property/set_reply|services_reply' \
    "${text_file}" > "${matches_file}" || true
  awk '
    /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].* IP / {
      endpoint = $0
      sub(/^.* IP /, "", endpoint)
      sub(/ >.*$/, "", endpoint)
      next
    }
    /thing\/product\/(EDGEGW001|DRONESIM001)\// {
      payload = $0
      if (endpoint == "") next
      signature = endpoint SUBSEP payload
      if (seen[signature]++) next
      category = "current_or_other"
      if (payload ~ /thing\/product\/EDGEGW001\/services_reply/ && index(payload, "\"result\": -2")) {
        category = "legacy_unknown_method_reply"
      } else if (payload ~ /thing\/product\/EDGEGW001\/property\/set_reply/ && !index(payload, "\"method\"")) {
        category = "legacy_methodless_property_reply"
      } else if (payload ~ /thing\/product\/EDGEGW001\/osd/ && index(payload, "\"value\"")) {
        category = "legacy_value_wrapped_dock_osd"
      }
      counts[endpoint SUBSEP category]++
    }
    END {
      for (key in counts) {
        split(key, parts, SUBSEP)
        printf "%s\t%s\t%d\n", parts[1], parts[2], counts[key]
      }
    }
  ' "${text_file}" | sort > "${summary_body}"
  {
    printf 'source_endpoint\tcategory\tunique_publish_count\n'
    cat "${summary_body}"
  } > "${summary_file}"
  rm -f "${summary_body}"
  # 完整抓包可能包含 MQTT CONNECT 凭据，只开放过滤后的匹配日志给普通用户。
  chmod 600 "${pcap}" "${text_file}"
  chmod 644 "${matches_file}" "${summary_file}"
  echo "[成功] MQTT 发布源抓包已停止并解析"
  echo "[发布源摘要] ${summary_file}"
  echo "[匹配日志] ${matches_file}"
  echo "[完整文本] ${text_file}"
  echo "[原始抓包] ${pcap}"
}

capture_status() {
  local directory pid_file capture_pid
  directory="$(capture_dir)"
  pid_file="${directory}/tcpdump.pid"
  if [[ -f "${pid_file}" ]]; then
    capture_pid="$(cat "${pid_file}")"
    if kill -0 "${capture_pid}" 2>/dev/null; then
      echo "[信息] MQTT 发布源抓包运行中：PID=${capture_pid}，目录=${directory}"
      return 0
    fi
  fi
  echo "[信息] MQTT 发布源抓包未运行：目录=${directory}"
}

list_emqx_clients() {
  local container output filter
  container="$(emqx_container)"
  filter="${1:-EDGEGW001|dock3_sim|edge_gateway}"
  if [[ -z "${container}" ]]; then
    echo "[错误] 未发现正在运行的 EMQX Docker 容器。" >&2
    return 1
  fi
  echo "[信息] EMQX 容器：${container}"
  echo "[信息] 在线客户端过滤条件：${filter}"
  if ! output="$(emqx_ctl "${container}" clients list 2>&1)"; then
    echo "[错误] EMQX 客户端查询失败：" >&2
    printf '%s\n' "${output}" >&2
    return 3
  fi
  if ! printf '%s\n' "${output}" | grep -E "${filter}"; then
    echo "[警告] 没有客户端匹配过滤条件，以下为客户端列表前 80 行：" >&2
    printf '%s\n' "${output}" | sed -n '1,80p'
    return 2
  fi
}

diagnose_broker() {
  echo "[诊断] systemd 服务状态："
  systemctl --no-pager --full status "${BROKER_SERVICE}" 2>&1 | sed -n '1,24p' || true
  echo "[诊断] ${BROKER_PORT} 端口监听者："
  ss -ltnp 2>&1 | grep -E ":${BROKER_PORT}([[:space:]]|$)" || true
  echo "[诊断] MQTT broker 相关进程："
  pgrep -af 'mosquitto|nanomq|emqx' || true
  echo "[诊断] MQTT broker 相关容器："
  docker ps --no-trunc --format '{{.ID}} {{.Image}} {{.Names}} {{.Ports}}' 2>/dev/null \
    | grep -Ei 'emqx|mosquitto|nanomq' || true
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
  clients)
    list_emqx_clients "${CLIENT_ID}"
    ;;
  capture-start)
    capture_start
    ;;
  capture-stop)
    capture_stop
    ;;
  capture-status)
    capture_status
    ;;
  client)
    if [[ -z "${CLIENT_ID}" ]]; then
      echo "[错误] client 模式需要 MQTT client_id" >&2
      echo "用法：$0 client <client_id>" >&2
      exit 2
    fi
    emqx_name="$(emqx_container)"
    if [[ -n "${emqx_name}" ]]; then
      echo "[信息] 查询 EMQX client_id：${CLIENT_ID}"
      emqx_ctl "${emqx_name}" clients show "${CLIENT_ID}" || true
      echo "[提示] 确认是旧客户端后，可用以下命令临时断开："
      echo "       sudo docker exec -u emqx ${emqx_name} /opt/emqx/bin/emqx ctl clients kick ${CLIENT_ID}"
      echo "[提示] 若客户端自动重连，必须根据 peername 在来源主机停止旧程序。"
      exit 0
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
    echo "用法：$0 <enable|disable|status|diagnose|clients|client|capture-start|capture-stop|capture-status> [argument]" >&2
    exit 2
    ;;
esac
