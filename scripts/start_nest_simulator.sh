#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

NEST_HOST="${NEST_HOST:-0.0.0.0}"
NEST_PORT="${NEST_PORT:-$(nest_port)}"

echo "[INFO] MAVLink nest simulator"
echo "[INFO] bind=${NEST_HOST}:${NEST_PORT}"
echo "[INFO] NEST_HOST is the simulator bind address; gateway nest_mavlink.connection.host must be a reachable target such as 127.0.0.1 or the simulator host IP, not 0.0.0.0"
echo "[INFO] if SITL also uses 14550, keep nest on 14551 and config nest_mavlink.connection.port aligned"

cd "${PROJECT_ROOT}"
exec "${PROJECT_PYTHON}" "${PROJECT_ROOT}/src/nest_simulator.py" --host "${NEST_HOST}" --port "${NEST_PORT}"
