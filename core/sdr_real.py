"""
OPHIR SDR Real Module
Extends core/sdr.py with live aircraft tracking, noise monitoring and signal events.
Connects to dump1090 SBS format (TCP port 30001) and keeps a live aircraft_dict.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
from datetime import datetime

from core.sdr import SDRReader as _BaseSDRReader

logger = logging.getLogger(__name__)

# Seconds without an update before an aircraft is removed from the live dict
_AIRCRAFT_TTL = 60

# Maximum number of signal events to keep in memory
_MAX_EVENTS = 200


def _age_seconds(timestamp_str: str) -> float:
    """Return how many seconds ago an ISO-format timestamp string was."""
    if not timestamp_str:
        return float("inf")
    try:
        ts = datetime.fromisoformat(timestamp_str)
        return (datetime.utcnow() - ts).total_seconds()
    except Exception:
        return float("inf")


class SDRReader(_BaseSDRReader):
    """
    Enhanced SDR reader for live aircraft tracking.

    Adds:
    - aircraft_dict  : dict[hex_code -> aircraft_data] kept current by a
                       background asyncio task that reads dump1090 SBS messages.
    - get_noise_data(): async; returns a dict with signal_type/confidence/noise_dbm.
    - get_signal_events(): async; returns a list of recent signal events.
    """

    def __init__(self):
        super().__init__()
        # Use SBS text port (30001) instead of the raw-frame port
        self.port = 30001

        # Live tracking state
        self.aircraft_dict: dict = {}
        self._signal_events: list = []

        # Try to schedule the background reader if an event loop is already running
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._read_loop())
                logger.info("SDRReader background task scheduled")
        except RuntimeError:
            logger.warning(
                "No running event loop at SDRReader init; "
                "call start_background_reader() from an async context."
            )

    def start_background_reader(self):
        """
        Explicitly start the background reading loop.
        Call this from an async context (e.g. FastAPI startup event) if the
        loop was not yet running when SDRReader was constructed.
        """
        loop = asyncio.get_event_loop()
        loop.create_task(self._read_loop())
        logger.info("SDRReader background task started")

    # ------------------------------------------------------------------
    # Internal background loop
    # ------------------------------------------------------------------

    async def _read_loop(self):
        """Continuously connect to dump1090 and ingest SBS messages."""
        while True:
            try:
                await self.connect()
                async for msg in self.read_messages():
                    if msg:
                        self._update_aircraft(msg)
                        self._record_signal_event(msg)
                # Connection dropped — reset flag and retry
                self.connected = False
            except Exception as e:
                logger.warning(f"SDR read loop error: {e}. Retrying in 5 s…")
                self.connected = False
            await asyncio.sleep(5)

    def _update_aircraft(self, msg: dict):
        """Merge an SBS message into aircraft_dict and expire stale entries."""
        hex_code = msg.get("hex_code")
        if not hex_code:
            return

        existing = self.aircraft_dict.get(hex_code, {"hex_code": hex_code})

        # Merge: only overwrite fields that have a non-None value in the new msg
        for key, value in msg.items():
            if value is not None:
                existing[key] = value

        existing["last_seen"] = datetime.utcnow().isoformat()
        self.aircraft_dict[hex_code] = existing

        # Expire aircraft that haven't been seen recently
        stale = [
            h
            for h, ac in self.aircraft_dict.items()
            if _age_seconds(ac.get("last_seen")) > _AIRCRAFT_TTL
        ]
        for h in stale:
            del self.aircraft_dict[h]

    def _record_signal_event(self, msg: dict):
        """Append a signal event; keep the list bounded."""
        event = {
            "hex_code": msg.get("hex_code"),
            "callsign": msg.get("callsign"),
            "timestamp": datetime.utcnow().isoformat(),
            "rssi": msg.get("rssi"),
        }
        self._signal_events.append(event)
        if len(self._signal_events) > _MAX_EVENTS:
            self._signal_events = self._signal_events[-_MAX_EVENTS:]

    # ------------------------------------------------------------------
    # Public async helpers used by main.py
    # ------------------------------------------------------------------

    async def get_noise_data(self) -> dict:
        """Return current signal / noise statistics derived from live data."""
        rssi_values = [
            ac.get("rssi")
            for ac in self.aircraft_dict.values()
            if ac.get("rssi") is not None
        ]
        avg_rssi = round(sum(rssi_values) / len(rssi_values), 1) if rssi_values else -80.0

        signal_type = "ADS-B" if self.aircraft_dict else "NOISE"
        confidence = min(100, len(self.aircraft_dict) * 10)

        return {
            "signal_type": signal_type,
            "confidence": confidence,
            "noise_dbm": avg_rssi,
            "aircraft_count": len(self.aircraft_dict),
        }

    async def get_signal_events(self) -> list:
        """Return the list of recent signal events (newest last)."""
        return list(self._signal_events)
