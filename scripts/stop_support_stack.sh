#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

PID_DIR="$(pid_dir)"

shopt -s nullglob
PID_FILES=("${PID_DIR}"/*.pid)

if [[ ${#PID_FILES[@]} -eq 0 ]]; then
  echo "[INFO] no pid files under ${PID_DIR}"
  exit 0
fi

for pid_file in "${PID_FILES[@]}"; do
  name="$(basename "${pid_file}" .pid)"
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    rm -f "${pid_file}"
    continue
  fi

  if kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[INFO] stopping ${name} pid=${pid}"
    kill "${pid}" >/dev/null 2>&1 || true
  else
    echo "[INFO] ${name} pid=${pid} is not running"
  fi
  rm -f "${pid_file}"
done

echo "[OK] support stack stop requested"
