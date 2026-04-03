#!/usr/bin/env python3
"""
OPHIR 2.0 | AEGIS-X — Single-Command Autonomous Launcher
Usage:  python3 run.py

Starts:
  1. dump1090 (basic mode, no FlightAware) on port 30001
  2. FastAPI server on http://0.0.0.0:8080
  3. All background tracking loops
  4. LLM analyzer (Ollama)
  5. Learning engine & distance calculator
"""

import asyncio
import ipaddress
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# ── Ensure repo root is on Python path ──────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/ophir_run.log"),
    ],
)
log = logging.getLogger("run")

# ── Banner ───────────────────────────────────────────────────────────────────
BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR               ║
║          Observer: 31.073541°N, 35.037383°E                 ║
║          Mode: AUTONOMOUS — no operator required            ║
╚══════════════════════════════════════════════════════════════╝
"""


def _local_ip() -> str:
    """Best-effort: return the WiFi/LAN IP visible to other devices."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _check_dependencies() -> bool:
    ok = True
    for pkg in ["fastapi", "uvicorn", "httpx", "sqlalchemy"]:
        try:
            __import__(pkg)
        except ImportError:
            log.error(f"Missing Python package: {pkg}  →  pip install {pkg}")
            ok = False
    return ok


def _start_dump1090() -> subprocess.Popen | None:
    """Start dump1090 in basic (non-FA) mode."""
    binary = shutil.which("dump1090") or shutil.which("dump1090-mutability")
    if not binary:
        log.warning("⚠️  dump1090 not found — install it or point $PATH at the binary.")
        log.warning("    Continuing without real SDR data (API will report no aircraft).")
        return None

    cmd = [
        binary,
        "--raw",
        "--net",
        "--net-only",
        "--quiet",
    ]
    log.info(f"🚀 Starting dump1090: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)  # let it initialise
        if proc.poll() is None:
            log.info(f"✅ dump1090 running (PID {proc.pid}) on port 30001")
            return proc
        else:
            log.warning("⚠️  dump1090 exited immediately — check installation")
            return None
    except Exception as e:
        log.warning(f"⚠️  Could not launch dump1090: {e}")
        return None


def _print_network_info(host_ip: str, port: int = 8080) -> None:
    print()
    print("━" * 62)
    print(f"  📡 Dashboard:  http://{host_ip}:{port}/dashboard.html")
    print(f"  🌐 API Root:   http://{host_ip}:{port}/")
    print(f"  📊 Swagger UI: http://{host_ip}:{port}/docs")
    print(f"  🔌 WebSocket:  ws://{host_ip}:{port}/api/v1/live/aircraft")
    print("━" * 62)
    print()


def main() -> None:
    print(BANNER)

    if not _check_dependencies():
        log.error("Install missing dependencies first:")
        log.error("  pip install -r requirements.txt")
        sys.exit(1)

    dump1090_proc = _start_dump1090()

    host_ip = _local_ip()
    _print_network_info(host_ip)

    log.info("🤖 Starting OPHIR 2.0 FastAPI server on http://0.0.0.0:8080 …")
    log.info("   Press Ctrl+C to shutdown gracefully.")

    try:
        import uvicorn
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8080,
            reload=False,
            workers=1,
            log_level="info",
        )
    except KeyboardInterrupt:
        log.info("🛑 Ctrl+C — shutting down…")
    finally:
        if dump1090_proc and dump1090_proc.poll() is None:
            log.info(f"🛑 Stopping dump1090 (PID {dump1090_proc.pid})")
            dump1090_proc.terminate()
            try:
                dump1090_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                dump1090_proc.kill()
        log.info("✅ OPHIR 2.0 shutdown complete.")


if __name__ == "__main__":
    main()
