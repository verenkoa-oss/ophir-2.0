#!/usr/bin/env python3
"""
OPHIR 2.0 – Autonomous Entry Point
====================================
Run this file to start the complete OPHIR 2.0 system:
    python3 run.py

What it does:
  1. Prints the local IP addresses so you know where to connect from the network
  2. Attempts to start dump1090 (via systemctl or direct binary)
  3. Starts the FastAPI / WebSocket server on 0.0.0.0:8080
  4. Opens the dashboard in the default browser (optional)

No operator intervention required after launch.
"""

import subprocess
import sys
import os
import time
import socket
import logging
from pathlib import Path

# Ensure the project root is on the Python path
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("run")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local_ips() -> list[str]:
    """Return all non-loopback IPv4 addresses."""
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for addr in socket.getaddrinfo(hostname, None):
            ip = addr[4][0]
            if not ip.startswith("127.") and ":" not in ip:
                if ip not in ips:
                    ips.append(ip)
    except Exception:
        pass
    # Fallback via UDP trick
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return ips or ["127.0.0.1"]


def _print_banner(port: int = 8080) -> None:
    ips = _local_ips()
    print()
    print("=" * 60)
    print("  🛰  OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR")
    print("=" * 60)
    print(f"  Observer: Lat {_OBSERVER_LAT}, Lon {_OBSERVER_LON}")
    print()
    print("  Dashboard URLs:")
    for ip in ips:
        print(f"    http://{ip}:{port}/")
    print(f"    http://localhost:{port}/")
    print()
    print("  API docs: http://localhost:{}/docs".format(port))
    print("=" * 60)
    print()


def _try_start_dump1090() -> bool:
    """Attempt to start dump1090 if not already running.

    Returns True if dump1090 appears to be running after the attempt.
    """
    # Check if already running
    try:
        result = subprocess.run(
            ["pgrep", "-x", "dump1090"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            logger.info("✅ dump1090 already running (PID %s)", result.stdout.strip())
            return True
    except FileNotFoundError:
        pass  # pgrep not available

    logger.info("🔄 Attempting to start dump1090...")

    # Try systemctl services
    for service in ("dump1090-fa", "dump1090-mutability", "dump1090"):
        try:
            r = subprocess.run(
                ["systemctl", "start", service],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                logger.info("✅ Started %s via systemctl", service)
                time.sleep(2)
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Try direct binary in BASIC (raw) mode
    for binary in ("dump1090", "dump1090-fa", "dump1090-mutability"):
        try:
            proc = subprocess.Popen(
                [binary, "--raw", "--net", "--quiet"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            if proc.poll() is None:
                logger.info("✅ Started %s (PID %d)", binary, proc.pid)
                return True
        except FileNotFoundError:
            continue

    logger.warning(
        "⚠️  Could not start dump1090 automatically.\n"
        "    Install it with: sudo apt-get install dump1090-fa\n"
        "    Or start manually: dump1090 --raw --net --quiet\n"
        "    OPHIR will keep retrying to connect."
    )
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Import config here so we can read the observer coordinates for the banner
try:
    import config as _cfg
    _OBSERVER_LAT = _cfg.OBSERVER_LAT
    _OBSERVER_LON = _cfg.OBSERVER_LON
    _PORT = _cfg.API_PORT
except Exception:
    _OBSERVER_LAT = 31.073541
    _OBSERVER_LON = 35.037383
    _PORT = 8080


def main() -> None:
    _print_banner(_PORT)
    _try_start_dump1090()

    # Start the FastAPI server (this call blocks until the server exits)
    logger.info("🚀 Starting OPHIR 2.0 FastAPI server on port %d …", _PORT)
    try:
        import uvicorn
        import main as _app_module
        uvicorn.run(
            _app_module.app,
            host="0.0.0.0",
            port=_PORT,
            workers=1,
            log_level="info",
        )
    except KeyboardInterrupt:
        logger.info("👋 OPHIR 2.0 stopped by user.")
    except ImportError as exc:
        logger.error("❌ Import error: %s", exc)
        logger.error("   Run: pip3 install -r requirements.txt")
        sys.exit(1)


if __name__ == "__main__":
    main()
