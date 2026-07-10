#!/usr/bin/env bash

# Run one Ubuntu acceptance command and keep a complete copy of its output.
# Usage:
#   bash scripts/run_test_with_log.sh --name wayline-real -- python tools/...

set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_ROOT="${TEST_LOG_DIR:-${PROJECT_ROOT}/logs/test_runs}"
NAME="test"

if [[ "${1:-}" == "--name" ]]; then
  NAME="${2:-}"
  shift 2
fi

if [[ "${1:-}" == "--" ]]; then
  shift
fi

if [[ -z "${NAME}" || "$#" -eq 0 ]]; then
  echo "用法: bash scripts/run_test_with_log.sh [--name 名称] -- <测试命令>" >&2
  exit 64
fi

safe_name="$(printf '%s' "${NAME}" | tr -cs '[:alnum:]_.-' '_')"
timestamp="$(date '+%Y%m%d_%H%M%S')"
mkdir -p "${LOG_ROOT}"
log_file="${LOG_ROOT}/${timestamp}_${safe_name}.log"

echo "[信息] 完整测试日志: ${log_file}"
if git -C "${PROJECT_ROOT}" rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "[信息] Git 提交: $(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
  if [[ -n "$(git -C "${PROJECT_ROOT}" status --porcelain)" ]]; then
    echo "[警告] 工作区存在未提交修改："
    git -C "${PROJECT_ROOT}" status --short
  else
    echo "[信息] 工作区状态: clean"
  fi
fi
"$@" 2>&1 | tee "${log_file}"
command_rc=${PIPESTATUS[0]}

echo "[信息] 测试退出码: ${command_rc}"
echo "[信息] 复制日志命令: sed -n '1,260p' ${log_file}"
exit "${command_rc}"
