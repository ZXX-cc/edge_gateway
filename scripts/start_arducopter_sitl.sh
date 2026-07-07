#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

ARDUPILOT_DIR="${ARDUPILOT_DIR:-${HOME}/lxz/ardupilot}"
SITL_PYTHON="${SITL_PYTHON:-/usr/bin/python3}"
SITL_VEHICLE="${SITL_VEHICLE:-ArduCopter}"
MAVLINK_PORT="${MAVLINK_PORT:-$(mavlink_port)}"
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
unset PYTHONHOME

echo "[INFO] ArduPilot SITL"
echo "[INFO] vehicle=${SITL_VEHICLE}, out=127.0.0.1:${MAVLINK_PORT}"
echo "[INFO] python=${SITL_PYTHON}"

cd "${ARDUPILOT_DIR}/ArduCopter"
exec "${SITL_PYTHON}" "${SIM_VEHICLE}" -v "${SITL_VEHICLE}" --out="127.0.0.1:${MAVLINK_PORT}"
