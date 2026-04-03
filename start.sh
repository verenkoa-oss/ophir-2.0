#!/usr/bin/env bash
# ============================================================
# OPHIR 2.0 | AEGIS-X — Bash Starter
# Usage:  bash start.sh
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR               ║"
echo "║          Observer: 31.073541°N, 35.037383°E                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Python check ────────────────────────────────────────────────────────────
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "❌ Python 3 not found. Please install Python 3.10+."
    exit 1
fi
PYVER=$($PYTHON -c "import sys; print(sys.version_info.major, sys.version_info.minor)")
echo "✅ Python: $($PYTHON --version)"

# ── Virtual environment (optional) ──────────────────────────────────────────
if [ -d "venv" ]; then
    echo "✅ Activating virtual environment …"
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo "✅ Activating virtual environment …"
    source .venv/bin/activate
fi

# ── Dependencies ─────────────────────────────────────────────────────────────
echo "🔍 Checking Python dependencies …"
if ! $PYTHON -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "📦 Installing dependencies …"
    $PYTHON -m pip install -r requirements.txt --quiet
fi

# ── dump1090 (best-effort) ────────────────────────────────────────────────────
if command -v dump1090 &>/dev/null || command -v dump1090-mutability &>/dev/null; then
    DUMP=$(command -v dump1090 || command -v dump1090-mutability)
    echo "🚀 Starting dump1090 (basic mode) …"
    $DUMP --raw --net --net-only --quiet 2>>/tmp/dump1090_error.log &
    DUMP_PID=$!
    sleep 1
    echo "✅ dump1090 PID=$DUMP_PID"
else
    echo "⚠️  dump1090 not found — SDR data will be unavailable"
fi

# ── Launch server ─────────────────────────────────────────────────────────────
echo ""
echo "🌐 Launching OPHIR 2.0 on http://0.0.0.0:8080 …"
echo ""

trap 'echo ""; echo "🛑 Shutting down..."; [ -n "$DUMP_PID" ] && kill "$DUMP_PID" 2>/dev/null; exit 0' INT TERM

$PYTHON run.py
