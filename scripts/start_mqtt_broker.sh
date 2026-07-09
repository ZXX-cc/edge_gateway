#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

BROKER_MODE="${BROKER_MODE:-systemd}"
BROKER_SERVICE="${BROKER_SERVICE:-mosquitto}"
BROKER_PORT="${BROKER_PORT:-$(mqtt_port)}"
MOSQUITTO_CONFIG="${MOSQUITTO_CONFIG:-/etc/mosquitto/mosquitto.conf}"
MOSQUITTO_CONFIG_CHECK_TIMEOUT="${MOSQUITTO_CONFIG_CHECK_TIMEOUT:-8s}"

port_listener_found() {
  if ! command -v ss >/dev/null 2>&1; then
    return 1
  fi
  ss -tlnp 2>/dev/null | grep -E ":${BROKER_PORT}\\b" >/dev/null
}

print_port_listener() {
  if command -v ss >/dev/null 2>&1; then
    echo "[INFO] listening sockets on :${BROKER_PORT}"
    ss -tlnp 2>/dev/null | grep -E ":${BROKER_PORT}\\b" || true
  else
    echo "[WARN] ss not found; cannot inspect TCP port ${BROKER_PORT}" >&2
  fi
}

print_systemd_diagnostics() {
  echo "[INFO] ${BROKER_SERVICE} status:"
  sudo systemctl status "${BROKER_SERVICE}" --no-pager -l || true
  if command -v journalctl >/dev/null 2>&1; then
    echo "[INFO] recent ${BROKER_SERVICE} journal:"
    sudo journalctl -u "${BROKER_SERVICE}" -n 80 --no-pager || true
  fi
}

print_mosquitto_config_files() {
  if [[ ! -f "${MOSQUITTO_CONFIG}" ]]; then
    echo "[WARN] mosquitto config not found: ${MOSQUITTO_CONFIG}" >&2
    return
  fi

  echo "[INFO] ${MOSQUITTO_CONFIG}:"
  sudo nl -ba "${MOSQUITTO_CONFIG}" || true

  local include_dir="/etc/mosquitto/conf.d"
  if [[ -d "${include_dir}" ]]; then
    echo "[INFO] ${include_dir}/*.conf:"
    while IFS= read -r conf_file; do
      [[ -n "${conf_file}" ]] || continue
      echo "[INFO] ${conf_file}:"
      sudo nl -ba "${conf_file}" || true
    done < <(find "${include_dir}" -maxdepth 1 -type f -name '*.conf' | sort)
  else
    echo "[WARN] mosquitto include dir not found: ${include_dir}" >&2
  fi
}

run_mosquitto_foreground_check() {
  if ! command -v mosquitto >/dev/null 2>&1; then
    echo "[WARN] mosquitto command not found; cannot run foreground config check" >&2
    return
  fi
  if ! command -v timeout >/dev/null 2>&1; then
    echo "[WARN] timeout command not found; skip foreground config check" >&2
    return
  fi

  echo "[INFO] foreground config check: sudo timeout ${MOSQUITTO_CONFIG_CHECK_TIMEOUT} mosquitto -c ${MOSQUITTO_CONFIG} -v"
  set +e
  sudo timeout "${MOSQUITTO_CONFIG_CHECK_TIMEOUT}" mosquitto -c "${MOSQUITTO_CONFIG}" -v
  local rc=$?
  set -e
  if [[ "${rc}" -eq 124 ]]; then
    echo "[INFO] foreground config check timed out; config parsed far enough to start and block"
  else
    echo "[INFO] foreground config check exit code: ${rc}"
  fi
}

echo "[INFO] MQTT broker mode: ${BROKER_MODE}"
echo "[INFO] expected broker port: ${BROKER_PORT}"

if [[ "${BROKER_MODE}" == "systemd" ]]; then
  require_command systemctl
  echo "[INFO] restarting ${BROKER_SERVICE}..."
  if ! sudo systemctl restart "${BROKER_SERVICE}"; then
    echo "[WARN] ${BROKER_SERVICE} restart failed" >&2
    print_systemd_diagnostics
    print_port_listener
    print_mosquitto_config_files
    run_mosquitto_foreground_check
    if port_listener_found; then
      echo "[WARN] continuing because a broker-like TCP listener is already present on ${BROKER_PORT}" >&2
      echo "[WARN] if this is intentional, run with BROKER_MODE=check to avoid restarting systemd" >&2
    else
      echo "[ERROR] no broker is listening on ${BROKER_PORT}" >&2
      echo "[ERROR] common checks: sudo mosquitto -c /etc/mosquitto/mosquitto.conf -v, sudo journalctl -xeu ${BROKER_SERVICE}" >&2
      exit 1
    fi
  else
    sudo systemctl status "${BROKER_SERVICE}" --no-pager -l || true
  fi
elif [[ "${BROKER_MODE}" == "check" ]]; then
  echo "[INFO] check-only mode; not starting or restarting broker"
else
  echo "[ERROR] unsupported BROKER_MODE=${BROKER_MODE}; use systemd or check" >&2
  exit 2
fi

print_port_listener
if ! port_listener_found; then
  echo "[ERROR] no TCP listener found on port ${BROKER_PORT}" >&2
  exit 1
fi

echo "[OK] MQTT broker is ready on port ${BROKER_PORT}"
