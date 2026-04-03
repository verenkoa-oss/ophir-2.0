#!/usr/bin/env bash
# OPHIR 2.0 – Linux / macOS launcher
# Usage: ./start.sh
# -------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Colour helpers ------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

info()    { echo -e "${GREEN}[OPHIR]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN ]${NC} $*"; }
error_exit() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

echo ""
echo -e "${CYAN}=================================================${NC}"
echo -e "${CYAN}  🛰  OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR${NC}"
echo -e "${CYAN}=================================================${NC}"
echo ""

# ---- Python check --------------------------------------------------
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
    error_exit "python3 not found. Please install Python 3.9+"
fi
PY_VER=$("$PYTHON" -c "import sys; print(sys.version_info[:2])")
info "Python: $("$PYTHON" --version) ($PY_VER)"

# ---- Virtual environment (optional) --------------------------------
if [ -d "venv" ]; then
    info "Activating virtual environment …"
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

# ---- Install dependencies (if requirements.txt present) ------------
if [ -f "requirements.txt" ]; then
    info "Checking / installing dependencies …"
    "$PYTHON" -m pip install -q -r requirements.txt || warn "pip install had warnings."
fi

# ---- Create required directories -----------------------------------
mkdir -p logs db data/ophir_db data/ophir_cache data/ophir_archive

# ---- Start system --------------------------------------------------
info "Launching OPHIR 2.0 …"
exec "$PYTHON" run.py "$@"
