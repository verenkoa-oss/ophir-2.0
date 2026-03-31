"""
OPHIR SDR Module
Reads ADS-B data from dump1090 via TCP BaseStation port (30003)
"""

import asyncio
import logging
from typing import Callable

import config

logger = logging.getLogger(__name__)


class SDRReader:
    """Read ADS-B messages from a dump1090 BaseStation feed."""

    def __init__(self, on_aircraft: Callable[[dict], None] | None = None):
        self.host = config.DUMP1090_HOST
        self.port = config.DUMP1090_PORT
        self.on_aircraft = on_aircraft
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        """Start background reading task."""
        self._running = True
        self._task = asyncio.create_task(self._read_loop())

    async def stop(self):
        """Stop background reading task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _read_loop(self):
        while self._running:
            try:
                reader, _ = await asyncio.open_connection(self.host, self.port)
            except (ConnectionRefusedError, OSError) as exc:
                logger.error("❌ Failed to connect to dump1090: %s", exc)
                self._running = False
                return

            while self._running:
                line = await reader.readline()
                if not line:
                    logger.warning("dump1090 connection closed, reconnecting…")
                    break
                message = line.decode("utf-8", errors="ignore").strip()
                aircraft = self._parse_message(message)
                if aircraft and self.on_aircraft:
                    self.on_aircraft(aircraft)

    @staticmethod
    def _parse_message(message: str) -> dict | None:
        """Parse a BaseStation format message into an aircraft dict."""
        parts = message.split(",")
        if len(parts) < 22:
            return None
        if parts[0] != "MSG":
            return None
        try:
            aircraft = {
                "hex": parts[4].strip(),
                "callsign": parts[10].strip() or None,
                "altitude": int(parts[11]) if parts[11].strip() else None,
                "speed": float(parts[12]) if parts[12].strip() else None,
                "track": float(parts[13]) if parts[13].strip() else None,
                "lat": float(parts[14]) if parts[14].strip() else None,
                "lon": float(parts[15]) if parts[15].strip() else None,
                "vertical_rate": int(parts[16]) if parts[16].strip() else None,
            }
            return aircraft
        except (ValueError, IndexError):
            return None
