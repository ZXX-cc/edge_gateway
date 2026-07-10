#!/usr/bin/env bash

# Run one Ubuntu acceptance command and keep a complete copy of its output.
# Usage:
#   bash scripts/run_test_with_log.sh --name wayline-real -- python tools/...
#   bash scripts/run_test_with_log.sh --name wayline-real --tail-log /tmp/edge_gateway.log -- python tools/...

set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_ROOT="${TEST_LOG_DIR:-${PROJECT_ROOT}/logs/test_runs}"
NAME="test"
TAIL_LOGS=()
TAIL_LINES=240

while [[ "$#" -gt 0 ]]; do
  case "${1:-}" in
    --name)
      NAME="${2:-}"
      shift 2
      ;;
    --tail-log)
      TAIL_LOGS+=("${2:-}")
      shift 2
      ;;
    --tail-lines)
      TAIL_LINES="${2:-}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ -z "${NAME}" || "$#" -eq 0 || ! "${TAIL_LINES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "用法: bash scripts/run_test_with_log.sh [--name 名称] [--tail-log 日志文件] [--tail-lines 行数] -- <测试命令>" >&2
  exit 64
fi

safe_name="$(printf '%s' "${NAME}" | tr -cs '[:alnum:]_.-' '_')"
timestamp="$(date '+%Y%m%d_%H%M%S')"
mkdir -p "${LOG_ROOT}"
log_file="${LOG_ROOT}/${timestamp}_${safe_name}.log"

{
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
  "$@"
  command_rc=$?
  for tail_log in "${TAIL_LOGS[@]}"; do
    echo "[信息] 网关日志尾部: ${tail_log}（最近 ${TAIL_LINES} 行）"
    if [[ -r "${tail_log}" ]]; then
      tail -n "${TAIL_LINES}" "${tail_log}"
    else
      echo "[警告] 无法读取日志文件: ${tail_log}"
    fi
  done
  echo "[信息] 测试退出码: ${command_rc}"
  echo "[信息] 复制日志命令: sed -n '1,260p' ${log_file}"
  exit "${command_rc}"
} 2>&1 | tee "${log_file}"
command_rc=${PIPESTATUS[0]}

exit "${command_rc}"
