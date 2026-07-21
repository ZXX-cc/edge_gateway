#!/usr/bin/env bash
set -euo pipefail

export DEMO_HOST="${DEMO_HOST:-0.0.0.0}"
export DEMO_PORT="${DEMO_PORT:-8095}"
export MQTT_HOST="${MQTT_HOST:-192.168.1.100}"
export MQTT_PORT="${MQTT_PORT:-1883}"
export GATEWAY_SN="${GATEWAY_SN:-EDGEGW001}"
export DRONE_SN="${DRONE_SN:-DRONESIM001}"

cd "$(dirname "$0")/.."
python demo_platform/app.py
