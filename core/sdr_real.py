"""
OPHIR SDR Real Module
Wrapper around SDRReader from core/sdr.py with live tracking state,
aircraft dictionary, noise data, and signal event streaming.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
import random
from datetime import datetime, timezone
from collections import deque
from core.sdr import SDRReader as _BaseSDRReader

logger = logging.getLogger(__name__)

# Re-export base class so callers can also import SDRReader from here
SDRReader = _BaseSDRReader


class LiveSDRReader(_BaseSDRReader):
    """
    Extended SDRReader with live tracking state management.

    Additional attributes / methods expected by main.py:
      - aircraft_dict   : dict of hex_code -> aircraft data
      - get_noise_data(): async, returns current noise/signal info
      - get_signal_events(): async, returns recent signal events list
    """

    def __init__(self):
        super().__init__()
        # Live aircraft dictionary: hex_code -> latest data dict
        self._aircraft_dict: dict = {}
        # Circular buffer of the last 200 signal events
        self._signal_events: deque = deque(maxlen=200)
        # Background reader task handle
        self._reader_task: asyncio.Task | None = None
        # Start background monitoring
        self._start_background_reader()

    # ------------------------------------------------------------------
    # Public interface expected by main.py
    # ------------------------------------------------------------------

    @property
    def aircraft_dict(self) -> dict:
        """Return live aircraft tracking dictionary."""
        return self._aircraft_dict

    async def get_noise_data(self) -> dict:
        """
        Return current noise / signal classification snapshot.
        When a real dump1090 connection is active, derives stats from
        the latest received messages.  Falls back to simulated values
        when not connected (development / offline mode).
        """
        if self.connected and self._aircraft_dict:
            rssi_values = [
                ac.get("rssi")
                for ac in self._aircraft_dict.values()
                if ac.get("rssi") is not None
            ]
            if rssi_values:
                avg_rssi = sum(rssi_values) / len(rssi_values)
                snr = avg_rssi + 100  # rough SNR approximation
                signal_type = "ADS-B" if avg_rssi > -80 else "NOISE"
                confidence = min(100, max(0, int((avg_rssi + 100) * 1.5)))
                return {
                    "signal_type": signal_type,
                    "confidence": confidence,
                    "noise_dbm": round(avg_rssi - snr, 1),
                    "snr": round(snr, 1),
                    "aircraft_count": len(self._aircraft_dict),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        # Offline / no-data fallback
        return {
            "signal_type": "SEARCHING",
            "confidence": 0,
            "noise_dbm": -100.0,
            "snr": 0.0,
            "aircraft_count": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_signal_events(self) -> list:
        """Return recent signal events as a plain list."""
        return list(self._signal_events)

    # ------------------------------------------------------------------
    # Internal background reader
    # ------------------------------------------------------------------

    def _start_background_reader(self):
        """Schedule the continuous reader coroutine on the running loop (if any)."""
        try:
            loop = asyncio.get_running_loop()
            self._reader_task = loop.create_task(self._continuous_read())
        except RuntimeError:
            # No running event loop yet (e.g., imported at module level).
            # The task will be created when the loop starts via on_event("startup").
            pass

    async def _continuous_read(self):
        """Continuously read messages from dump1090 and update state."""
        logger.info("🔄 Background SDR reader starting…")
        while True:
            try:
                if not self.connected:
                    await self.connect()

                async for msg in self.read_messages():
                    if msg and msg.get("hex_code"):
                        self._update_aircraft(msg)
                        self._record_event(msg)

            except Exception as exc:
                logger.warning(f"SDR reader error (will retry in 5 s): {exc}")
                self.connected = False
                await asyncio.sleep(5)

    def _update_aircraft(self, msg: dict):
        """Merge a parsed SBS message into the aircraft dictionary."""
        hex_code = msg["hex_code"]
        existing = self._aircraft_dict.get(hex_code, {})
        # Keep fields that are not None
        merged = {**existing}
        for key, value in msg.items():
            if value is not None:
                merged[key] = value
        merged["last_seen"] = datetime.now(timezone.utc).isoformat()
        if "first_seen" not in merged:
            merged["first_seen"] = merged["last_seen"]
        self._aircraft_dict[hex_code] = merged

    def _record_event(self, msg: dict):
        """Append a signal event to the circular event buffer."""
        event = {
            "hex_code": msg.get("hex_code"),
            "callsign": msg.get("callsign"),
            "rssi": msg.get("rssi"),
            "altitude": msg.get("altitude"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "ADS-B",
        }
        self._signal_events.append(event)


# Module-level singleton created lazily
_instance: LiveSDRReader | None = None


def get_sdr_reader() -> LiveSDRReader:
    """Return the module-level LiveSDRReader singleton."""
    global _instance
    if _instance is None:
        _instance = LiveSDRReader()
    return _instance
