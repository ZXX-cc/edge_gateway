#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${PROJECT_ROOT}/config/gateway.yaml}"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PROJECT_PYTHON="${PYTHON:-${PROJECT_ROOT}/.venv/bin/python}"
else
  PROJECT_PYTHON="${PYTHON:-python3}"
fi

config_value() {
  local key="$1"
  local default_value="${2:-}"

  "${PROJECT_PYTHON}" - "${CONFIG_FILE}" "${key}" "${default_value}" <<'PY' 2>/dev/null || printf '%s\n' "${default_value}"
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
key = sys.argv[2]
default = sys.argv[3]

try:
    import yaml
except Exception:
    print(default)
    raise SystemExit(0)

try:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
except Exception:
    print(default)
    raise SystemExit(0)

value = data
for part in key.split("."):
    if not isinstance(value, dict) or part not in value:
        print(default)
        raise SystemExit(0)
    value = value[part]

if value is None:
    print(default)
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

gateway_sn() {
  config_value "gateway.sn" "EDGEGW001"
}

mqtt_host() {
  config_value "nanomq.host" "127.0.0.1"
}

mqtt_port() {
  config_value "nanomq.port" "1883"
}

mavlink_port() {
  config_value "mavlink.connection.port" "14550"
}

nest_port() {
  config_value "nest_mavlink.connection.port" "14551"
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] missing command: ${command_name}" >&2
    return 127
  fi
}

support_log_dir() {
  local dir="${PROJECT_ROOT}/logs/support/$(date +%Y-%m-%d_%H%M%S)"
  mkdir -p "${dir}"
  printf '%s\n' "${dir}"
}

pid_dir() {
  local dir="${PROJECT_ROOT}/.runtime/pids"
  mkdir -p "${dir}"
  printf '%s\n' "${dir}"
}
