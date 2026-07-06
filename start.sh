#!/bin/bash
# ============================================================
# 边缘网关启动脚本
# 树莓派 + NanoMQ + MAVLink
# ============================================================

set -e

# --- 配置 ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${PROJECT_DIR}/config/gateway.yaml"
CONFIG_DEST="${EDGE_GATEWAY_CONFIG_DEST:-/etc/edge_gateway/gateway.yaml}"
VENV_DIR="${PROJECT_DIR}/.venv"
REQUIREMENTS_FILE="${EDGE_GATEWAY_REQUIREMENTS:-${PROJECT_DIR}/requirements-ubuntu.txt}"
if [ ! -f "$REQUIREMENTS_FILE" ]; then
    REQUIREMENTS_FILE="${PROJECT_DIR}/requirements.txt"
fi
LOG_DIR="${EDGE_GATEWAY_LOG_DIR:-/var/log/edge_gateway}"
PID_FILE="${EDGE_GATEWAY_PID_FILE:-/var/run/edge_gateway.pid}"

# --- 颜色输出 ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- 检查依赖 ---
check_dependencies() {
    log_info "Checking dependencies..."

    # 检查Python3
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 not found. Please install Python 3.8+"
        exit 1
    fi

    # 检查NanoMQ
    if ! command -v nanomq &> /dev/null; then
        log_warn "NanoMQ not found. Attempting to install..."
        install_nanomq
    fi

    # 检查pip
    if ! command -v pip3 &> /dev/null; then
        log_error "pip3 not found. Please install pip."
        exit 1
    fi
}

# --- 安装NanoMQ ---
install_nanomq() {
    log_info "Installing NanoMQ..."
    # 检测架构
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "armv7l" ]; then
        # ARM架构(树莓派)
        if command -v apt &> /dev/null; then
            curl -sSL https://raw.githubusercontent.com/nanomq/nanomq/master/.github/scripts/install_nanomq.sh | bash
        else
            log_error "Unsupported package manager. Please install NanoMQ manually."
            exit 1
        fi
    else
        log_info "Please install NanoMQ manually for architecture: $ARCH"
        exit 1
    fi
}

# --- 创建虚拟环境 ---
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        log_info "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi

    source "${VENV_DIR}/bin/activate"

    # 安装依赖
    log_info "Installing Python dependencies from ${REQUIREMENTS_FILE}..."
    python3 -m pip install -q --upgrade pip setuptools wheel
    python3 -m pip install -q -r "${REQUIREMENTS_FILE}"
}

# --- 创建必要目录 ---
setup_directories() {
    log_info "Setting up directories..."
    mkdir -p "$LOG_DIR"
    mkdir -p "/data/media"
    mkdir -p "/data/nanomq"
    mkdir -p "/etc/edge_gateway"

    # 复制默认配置(如果不存在)
    if [ ! -f "$CONFIG_DEST" ]; then
        cp "$CONFIG_FILE" "$CONFIG_DEST"
        log_info "Default config copied to $CONFIG_DEST"
    fi
}

# --- 启动NanoMQ ---
start_nanomq() {
    log_info "Starting NanoMQ..."

    # 检查NanoMQ是否已在运行
    if pgrep -x "nanomq" > /dev/null; then
        log_warn "NanoMQ already running"
        return 0
    fi

    # 替换环境变量
    export GATEWAY_SN=$(grep 'sn:' "$CONFIG_FILE" | awk '{print $2}' | tr -d '"')
    envsubst < "${PROJECT_DIR}/nanomq.conf" > /tmp/nanomq_runtime.conf

    # 启动NanoMQ
    nanomq start --conf /tmp/nanomq_runtime.conf &
    sleep 2

    if pgrep -x "nanomq" > /dev/null; then
        log_info "NanoMQ started successfully"
    else
        log_error "NanoMQ failed to start"
        exit 1
    fi
}

# --- 停止NanoMQ ---
stop_nanomq() {
    log_info "Stopping NanoMQ..."
    pkill -x "nanomq" 2>/dev/null || true
}

# --- 启动网关 ---
start_gateway() {
    log_info "Starting Edge Gateway..."

    # 检查是否已在运行
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        log_warn "Edge Gateway already running (PID: $(cat $PID_FILE))"
        return 0
    fi

    source "${VENV_DIR}/bin/activate"

    # 启动网关程序
    nohup python3 "${PROJECT_DIR}/src/main.py" \
        -c "$CONFIG_DEST" \
        >> "${LOG_DIR}/edge_gateway.log" 2>&1 &

    echo $! > "$PID_FILE"
    sleep 2

    if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        log_info "Edge Gateway started (PID: $(cat $PID_FILE))"
    else
        log_error "Edge Gateway failed to start"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# --- 停止网关 ---
stop_gateway() {
    log_info "Stopping Edge Gateway..."
    if [ -f "$PID_FILE" ]; then
        kill $(cat "$PID_FILE") 2>/dev/null || true
        rm -f "$PID_FILE"
    fi
}

# --- 状态检查 ---
status() {
    echo "=== Edge Gateway Status ==="

    # NanoMQ状态
    if pgrep -x "nanomq" > /dev/null; then
        echo -e "NanoMQ: ${GREEN}Running${NC}"
    else
        echo -e "NanoMQ: ${RED}Stopped${NC}"
    fi

    # 网关状态
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo -e "Gateway: ${GREEN}Running${NC} (PID: $(cat $PID_FILE))"
    else
        echo -e "Gateway: ${RED}Stopped${NC}"
    fi

    # 端口监听
    echo ""
    echo "Listening ports:"
    ss -tlnp 2>/dev/null | grep -E "(1883|8083|14550|5000)" || true
}

# --- 查看日志 ---
logs() {
    if [ -f "${LOG_DIR}/edge_gateway.log" ]; then
        tail -n 100 -f "${LOG_DIR}/edge_gateway.log"
    else
        log_error "Log file not found"
    fi
}

# --- 主命令 ---
case "${1:-start}" in
    start)
        check_dependencies
        setup_venv
        setup_directories
        start_nanomq
        start_gateway
        log_info "All services started successfully!"
        ;;
    stop)
        stop_gateway
        stop_nanomq
        log_info "All services stopped"
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
    status)
        status
        ;;
    logs)
        logs
        ;;
    setup)
        check_dependencies
        setup_venv
        setup_directories
        log_info "Setup complete"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|setup}"
        exit 1
        ;;
esac
