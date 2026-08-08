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
  mqtt_endpoint_value "host" "127.0.0.1"
}

mqtt_port() {
  mqtt_endpoint_value "port" "1883"
}

mqtt_endpoint_value() {
  local field="$1"
  local default_value="${2:-}"

  "${PROJECT_PYTHON}" - "${CONFIG_FILE}" "${field}" "${default_value}" <<'PY' 2>/dev/null || printf '%s\n' "${default_value}"
import sys
from pathlib import Path
from urllib.parse import urlparse

config_path = Path(sys.argv[1])
field = sys.argv[2]
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


def get(path, fallback=None):
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return fallback
        value = value[part]
    return fallback if value in (None, "") else value


mode = str(get("mqtt.mode", get("nanomq.mode", "nano")) or "nano").strip().lower()
if mode not in {"nano", "cloud", "bridge"}:
    mode = "nano"

if mode == "cloud":
    host = get("mqtt.cloud.host", get("cloud.broker_host", "127.0.0.1"))
    port = get("mqtt.cloud.port", get("cloud.broker_port", "1883"))
elif mode == "bridge":
    host = get("mqtt.bridge.local.host", get("nanomq.host", "127.0.0.1"))
    port = get("mqtt.bridge.local.port", get("nanomq.port", "1883"))
else:
    host = get("mqtt.nano.host", get("nanomq.host", "127.0.0.1"))
    port = get("mqtt.nano.port", get("nanomq.port", "1883"))

if "://" in str(host):
    parsed = urlparse(str(host))
    if parsed.hostname:
        host = parsed.hostname
    if parsed.port:
        port = parsed.port

if field == "host":
    print(host)
elif field == "port":
    print(port)
else:
    print(default)
PY
}

mavlink_port() {
  config_value "mavlink.connection.port" "14550"
}

nest_port() {
  config_value "nest_mavlink.connection.port" "14551"
}

nest_sitl_host() {
  config_value "nest_mavlink.sitl.host" "127.0.0.1"
}

nest_sitl_port() {
  config_value "nest_mavlink.sitl.port" "14552"
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
