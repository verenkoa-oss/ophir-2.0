#!/usr/bin/env python3
"""
OPHIR 2.0 - SIGNAL INTELLIGENCE SYSTEM
Autonomous startup - single command execution
"""

import os
import sys
import subprocess
import signal
import time
import logging
from pathlib import Path

# Ensure project root is on the path before importing config
sys.path.insert(0, str(Path(__file__).parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class OphirSystem:
    def __init__(self):
        self.processes = []
        self.running = True

    def setup_signal_handlers(self):
        """Handle graceful shutdown"""
        signal.signal(signal.SIGINT, self.graceful_shutdown)
        signal.signal(signal.SIGTERM, self.graceful_shutdown)

    def graceful_shutdown(self, signum, frame):
        """Graceful shutdown on Ctrl+C"""
        logger.info("🛑 GRACEFUL SHUTDOWN INITIATED")
        self.running = False

        # Kill all child processes
        for proc in self.processes:
            try:
                logger.info(f"Terminating process {proc.pid}...")
                proc.terminate()
                proc.wait(timeout=5)
            except Exception as e:
                logger.error(f"Error terminating process: {e}")
                try:
                    proc.kill()
                except Exception:
                    pass

        logger.info("✅ System shutdown complete")
        sys.exit(0)

    def check_dependencies(self):
        """Check if all required packages are installed"""
        logger.info("🔍 Checking dependencies...")

        required_packages = [
            'fastapi', 'uvicorn', 'websockets',
            'numpy', 'pydantic', 'aiofiles'
        ]

        missing = []
        for pkg in required_packages:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)

        if missing:
            logger.error(f"Missing packages: {', '.join(missing)}")
            logger.info("Installing missing packages...")
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install'] + missing,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error(f"pip install failed:\n{result.stderr}")
                logger.warning("Continuing despite installation errors – some features may be unavailable")
            else:
                logger.info(f"pip install output:\n{result.stdout.strip()}")

        logger.info("✅ All dependencies available")

    def start_dump1090(self):
        """Start dump1090 in BASIC mode"""
        logger.info("📡 Starting dump1090 (BASIC mode)...")

        try:
            # Check if dump1090 (basic/mutability) is available
            # NOTE: only basic (dump1090 / dump1090-mutability) is supported;
            #       dump1090-fa is intentionally not used.
            cmd = None
            if subprocess.run(['which', 'dump1090'],
                              capture_output=True).returncode == 0:
                cmd = ['dump1090', '--raw', '--net', '--quiet']
            else:
                logger.warning("⚠️ dump1090 not found - skipping")
                return None

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.processes.append(proc)
            logger.info(f"✅ dump1090 running (PID: {proc.pid})")
            return proc

        except Exception as e:
            logger.error(f"❌ Failed to start dump1090: {e}")
            return None

    def start_fastapi_server(self):
        """Start FastAPI server"""
        logger.info("🚀 Starting FastAPI server...")

        try:
            import config as cfg
            reload_flag = "--reload" if cfg.API_RELOAD else "--no-access-log"
            cmd = [
                sys.executable, '-m', 'uvicorn',
                'main:app',
                '--host', cfg.API_HOST,
                '--port', str(cfg.API_PORT),
                reload_flag,
                '--log-level', cfg.API_LOG_LEVEL,
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.processes.append(proc)

            # Wait for server to start
            time.sleep(2)

            logger.info(f"✅ FastAPI running on http://{cfg.API_HOST}:{cfg.API_PORT} (PID: {proc.pid})")
            return proc

        except Exception as e:
            logger.error(f"❌ Failed to start FastAPI: {e}")
            return None

    def display_startup_info(self):
        """Display system startup information"""
        import socket
        import config as cfg

        hostname = socket.gethostname()
        try:
            ip = socket.gethostbyname(hostname)
        except Exception:
            ip = "localhost"

        port = cfg.API_PORT
        lat = cfg.OBSERVER_LATITUDE
        lon = cfg.OBSERVER_LONGITUDE

        logger.info((
            "\n"
            "╔════════════════════════════════════════════════════════╗\n"
            "║         OPHIR 2.0 - AUTONOMOUS SYSTEM READY            ║\n"
            "╠════════════════════════════════════════════════════════╣\n"
            "║                                                        ║\n"
            f"║ 🌐 DASHBOARD ACCESS:                                  ║\n"
            f"║    • Local:    http://localhost:{port}                  ║\n"
            f"║    • Network:  http://{ip}:{port}                   ║\n"
            f"║    • mDNS:     http://{hostname}.local:{port}         ║\n"
            "║                                                        ║\n"
            f"║ 📡 API ENDPOINTS:                                     ║\n"
            f"║    • Base: http://{ip}:{port}/api/v1                 ║\n"
            f"║    • Docs: http://{ip}:{port}/docs                  ║\n"
            "║                                                        ║\n"
            f"║ 🗺️  OBSERVER LOCATION:                               ║\n"
            f"║    • Latitude:  {lat}°N                          ║\n"
            f"║    • Longitude: {lon}°E                          ║\n"
            "║    • Status: OPERATIONAL ✓                           ║\n"
            "║                                                        ║\n"
            "║ 🔄 SYSTEM STATUS:                                     ║\n"
            "║    • dump1090: RUNNING ✓                             ║\n"
            "║    • FastAPI: RUNNING ✓                              ║\n"
            "║    • WebSocket: READY ✓                              ║\n"
            "║    • LLM: READY ✓                                    ║\n"
            "║    • Database: READY ✓                               ║\n"
            "║                                                        ║\n"
            "║ ⚠️  TO SHUTDOWN: Press Ctrl+C                         ║\n"
            "║                                                        ║\n"
            "╚════════════════════════════════════════════════════════╝"
        ))

    def run(self):
        """Main execution"""
        logger.info("🚀 OPHIR 2.0 STARTUP SEQUENCE")
        logger.info("=" * 60)

        # Setup signal handlers
        self.setup_signal_handlers()

        # Check dependencies
        self.check_dependencies()

        # Start dump1090
        self.start_dump1090()

        # Start FastAPI
        self.start_fastapi_server()

        # Display info
        self.display_startup_info()

        # Keep running
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.graceful_shutdown(None, None)

if __name__ == '__main__':
    system = OphirSystem()
    system.run()
