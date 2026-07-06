#!/usr/bin/env bash
# Non-mutating Ubuntu deployment preflight for edge_gateway.

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=0
WARNINGS=0

ok() { printf '[OK] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '[FAIL] %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
info() { printf '[INFO] %s\n' "$1"; }

check_command() {
    local cmd="$1"
    local required="${2:-required}"
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "command available: $cmd"
    elif [ "$required" = "required" ]; then
        fail "missing required command: $cmd"
    else
        warn "missing optional command: $cmd"
    fi
}

cd "$ROOT_DIR" || exit 1
info "repo: $ROOT_DIR"
info "this script only reads files and system state; it does not install, start, or edit anything"

if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" = "ubuntu" ]; then
        ok "Ubuntu detected: ${PRETTY_NAME:-unknown}"
    else
        warn "not Ubuntu: ${PRETTY_NAME:-unknown}; deployment docs target Ubuntu 22.04/24.04"
    fi
else
    warn "/etc/os-release not found"
fi

info "architecture: $(uname -m)"

check_command python3 required
check_command git optional
check_command curl optional
check_command envsubst optional
check_command sqlite3 optional
check_command ss optional
check_command nanomq optional
check_command mosquitto_pub optional
check_command mosquitto_sub optional
check_command sim_vehicle.py optional

if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import sys
major, minor = sys.version_info[:2]
print(f"[INFO] python: {sys.version.split()[0]}")
if (major, minor) < (3, 10):
    print("[WARN] Python 3.10+ is recommended for Ubuntu deployment")
else:
    print("[OK] Python version is deployment-ready")
PY
fi

for path in config/gateway.yaml nanomq.conf start.sh src/main.py requirements-ubuntu.txt; do
    if [ -f "$path" ]; then
        ok "file exists: $path"
    else
        fail "missing file: $path"
    fi
done

if [ -f README.md ] && grep -qE '^(<<<<<<<|=======|>>>>>>>)' README.md; then
    warn "README.md contains unresolved merge conflict markers"
fi

python3 - <<'PY'
from pathlib import Path
import ast
import sys

failures = 0

def report(kind, msg):
    print(f"[{kind}] {msg}")

req = Path("requirements-ubuntu.txt")
if req.exists():
    try:
        text = req.read_text(encoding="utf-8")
        report("OK", "requirements-ubuntu.txt is UTF-8")
        if "file:///" in text:
            report("FAIL", "requirements-ubuntu.txt contains local file:/// dependency")
            failures += 1
        else:
            report("OK", "requirements-ubuntu.txt has no local file:/// dependency")
        if "pydantic" not in text.lower():
            report("WARN", "pydantic is not listed; database schemas import pydantic v2 APIs")
    except Exception as exc:
        report("FAIL", f"cannot read requirements-ubuntu.txt as UTF-8: {exc}")
        failures += 1

original = Path("requirements.txt")
if original.exists():
    raw = original.read_bytes()
    try:
        raw.decode("utf-8")
        report("OK", "requirements.txt is UTF-8")
    except UnicodeDecodeError:
        report("WARN", "requirements.txt is not UTF-8; use requirements-ubuntu.txt for Ubuntu installs")

bad = []
for path in Path("src").rglob("*.py"):
    if "__pycache__" in path.parts:
        continue
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        bad.append((path, exc))
if bad:
    for path, exc in bad[:20]:
        report("FAIL", f"syntax parse failed: {path}: {exc}")
    failures += len(bad)
else:
    report("OK", "all src/*.py files parse without syntax errors")

main = Path("src/main.py")
if main.exists():
    mod = ast.parse(main.read_text(encoding="utf-8"))
    methods = []
    for node in ast.walk(mod):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register_batch":
            if node.args and isinstance(node.args[0], ast.Dict):
                for key in node.args[0].keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        methods.append(key.value)
    report("INFO", f"registered MQTT API methods: {len(methods)}")
    dup = len(methods) - len(set(methods))
    if dup:
        report("FAIL", f"duplicate MQTT API methods: {dup}")
        failures += 1
    else:
        report("OK", "no duplicate MQTT API methods detected")

sys.exit(1 if failures else 0)
PY
if [ $? -ne 0 ]; then
    FAILURES=$((FAILURES + 1))
fi

if command -v ss >/dev/null 2>&1; then
    for port in 1883 8083 14550; do
        if ss -tuln 2>/dev/null | grep -q ":$port "; then
            warn "port appears in use: $port"
        else
            ok "port appears free: $port"
        fi
    done
fi

printf '\nSummary: %s failure(s), %s warning(s)\n' "$FAILURES" "$WARNINGS"
if [ "$FAILURES" -ne 0 ]; then
    exit 1
fi

