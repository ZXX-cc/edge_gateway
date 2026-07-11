#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

ARDUPILOT_DIR="${ARDUPILOT_DIR:-${HOME}/lxz/ardupilot}"
SITL_PYTHON="${SITL_PYTHON:-/usr/bin/python3}"
SITL_VEHICLE="${SITL_VEHICLE:-ArduCopter}"
MAVLINK_PORT="${MAVLINK_PORT:-14552}"
SIM_VEHICLE="${ARDUPILOT_DIR}/Tools/autotest/sim_vehicle.py"

if [[ ! -f "${SIM_VEHICLE}" ]]; then
  echo "[ERROR] sim_vehicle.py not found: ${SIM_VEHICLE}" >&2
  exit 1
fi

if [[ ! -d "${ARDUPILOT_DIR}/ArduCopter" ]]; then
  echo "[ERROR] ArduCopter directory not found: ${ARDUPILOT_DIR}/ArduCopter" >&2
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

echo "[INFO] ArduPilot SITL"
echo "[INFO] vehicle=${SITL_VEHICLE}, out=127.0.0.1:${MAVLINK_PORT}"
echo "[INFO] python=${SITL_PYTHON}"

cd "${ARDUPILOT_DIR}/ArduCopter"
exec "${SITL_PYTHON}" "${SIM_VEHICLE}" -v "${SITL_VEHICLE}" --out="127.0.0.1:${MAVLINK_PORT}"
