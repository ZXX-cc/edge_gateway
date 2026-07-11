#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

ARDUPILOT_DIR="${ARDUPILOT_DIR:-${HOME}/lxz/ardupilot}"
SITL_PYTHON="${SITL_PYTHON:-/usr/bin/python3}"
SITL_VEHICLE="${SITL_VEHICLE:-ArduPlane}"
SITL_FRAME="${SITL_FRAME:-quadplane}"
SITL_LOCATION="${SITL_LOCATION:-Changchun_HD}"
SITL_NEST_HOST="${SITL_NEST_HOST:-$(nest_sitl_host)}"
SITL_NEST_PORT="${SITL_NEST_PORT:-$(nest_sitl_port)}"
NEST_LINK_HOST="${NEST_LINK_HOST:-127.0.0.1}"
NEST_LINK_PORT="${NEST_LINK_PORT:-$(nest_port)}"
SITL_LOCAL_NEST_OUT="${SITL_LOCAL_NEST_OUT:-1}"
SITL_EXTRA_OUT_PORT="${SITL_EXTRA_OUT_PORT:-${NEST_LINK_PORT}}"
SIM_VEHICLE="${ARDUPILOT_DIR}/Tools/autotest/sim_vehicle.py"

if [[ ! -f "${SIM_VEHICLE}" ]]; then
  echo "[ERROR] sim_vehicle.py not found: ${SIM_VEHICLE}" >&2
  exit 1
fi

if [[ ! -d "${ARDUPILOT_DIR}/ArduPlane" ]]; then
  echo "[ERROR] ArduPlane directory not found: ${ARDUPILOT_DIR}/ArduPlane" >&2
  exit 1
fi

export PATH="${PATH}:${HOME}/.local/bin:${ARDUPILOT_DIR}/Tools/autotest"

# ArduPilot waf discovers "python" from PATH. If this script is launched while
# edge_gateway .venv is active, waf may pick .venv/bin/python3 and miss the
# user/system packages installed for ArduPilot, such as pexpect.
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  PATH="$(printf '%s' "${PATH}" | tr ':' '\n' | grep -vx "${VIRTUAL_ENV}/bin" | paste -sd ':' -)"
fi
PATH="$(printf '%s' "${PATH}" | tr ':' '\n' | grep -vx "${PROJECT_ROOT}/.venv/bin" | paste -sd ':' -)"
export PATH
export PYTHON="${SITL_PYTHON}"
export PYTHON3="${SITL_PYTHON}"
unset VIRTUAL_ENV
unset PYTHONHOME
unset PYTHONPATH

is_ipv4() {
  local ip="$1"
  [[ "${ip}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1

  local IFS=.
  local -a octets
  read -r -a octets <<<"${ip}"
  local octet
  for octet in "${octets[@]}"; do
    ((octet >= 0 && octet <= 255)) || return 1
  done
}

EXTRA_OUT_IPS=()
if [[ -v SITL_EXTRA_OUT_IPS ]]; then
  if [[ -n "${SITL_EXTRA_OUT_IPS}" ]]; then
    IFS=',' read -r -a EXTRA_OUT_IPS <<<"${SITL_EXTRA_OUT_IPS}"
  fi
else
  # 当前 Ubuntu 联调机的固定观察端；可用 SITL_EXTRA_OUT_IPS="" 关闭。
  EXTRA_OUT_IPS=("192.168.2.59")
fi

CMD=(
  "${SITL_PYTHON}" "${SIM_VEHICLE}"
  -v "${SITL_VEHICLE}"
  -f "${SITL_FRAME}"
  -L "${SITL_LOCATION}"
  --no-rebuild
  --no-extra-ports
  "--out=udp:${SITL_NEST_HOST}:${SITL_NEST_PORT}"
)

# 按当前联调命令保留 14551 本机镜像；14552 才是机巢模拟器解析
# SITL 标准 MAVLink 的主入口。可设 SITL_LOCAL_NEST_OUT=0 关闭镜像。
if [[ "${SITL_LOCAL_NEST_OUT}" == "1" || "${SITL_LOCAL_NEST_OUT}" == "true" ]]; then
  CMD+=("--out=udp:${NEST_LINK_HOST}:${NEST_LINK_PORT}")
fi

for ip in "${EXTRA_OUT_IPS[@]}"; do
  if is_ipv4 "${ip}"; then
    CMD+=("--out=udp:${ip}:${SITL_EXTRA_OUT_PORT}")
  else
    echo "[WARN] IP 格式不正确，已忽略: ${ip}"
  fi
done

echo "[INFO] ArduPilot SITL"
echo "[INFO] vehicle=${SITL_VEHICLE}, frame=${SITL_FRAME}, location=${SITL_LOCATION}"
echo "[INFO] SITL -> nest simulator=udp:${SITL_NEST_HOST}:${SITL_NEST_PORT}"
if [[ "${SITL_LOCAL_NEST_OUT}" == "1" || "${SITL_LOCAL_NEST_OUT}" == "true" ]]; then
  echo "[INFO] local nest-link mirror=udp:${NEST_LINK_HOST}:${NEST_LINK_PORT}"
fi
if ((${#EXTRA_OUT_IPS[@]} > 0)); then
  for ip in "${EXTRA_OUT_IPS[@]}"; do
    if is_ipv4 "${ip}"; then
      echo "[INFO] extra out=udp:${ip}:${SITL_EXTRA_OUT_PORT}"
    fi
  done
fi
echo "[INFO] python=${SITL_PYTHON}"
printf '[INFO] command:'
printf ' %q' "${CMD[@]}"
printf '\n'

cd "${ARDUPILOT_DIR}/ArduPlane"
exec "${CMD[@]}"
