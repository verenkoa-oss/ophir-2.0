"""
OPHIR SDR Real Module
Extends core/sdr.py with live aircraft tracking, noise monitoring and signal events.
Connects to dump1090 SBS format (TCP port 30003) and keeps a live aircraft_dict.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime

import config
from core.sdr import SDRReader as _BaseSDRReader

logger = logging.getLogger(__name__)

# Seconds without an update before an aircraft is removed from the live dict
_AIRCRAFT_TTL = 60

# Maximum number of signal events to keep in memory
_MAX_EVENTS = 200

# Maximum noise history entries
_NOISE_HISTORY_MAXLEN = 200


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
    - aircraft_dict      : dict[hex_code -> aircraft_data] kept current by a
                           background asyncio task.
    - _noise_history     : deque of recent {noise_dbm, timestamp} snapshots.
    - set_antenna_mode() : switch antenna profile at runtime.
    - start_tracking()   : coroutine; start the background read loop.
    - stop_tracking()    : stop the background read loop.
    - get_noise_data()   : async; returns signal/noise statistics.
    - get_signal_events(): async; returns recent signal events.
    """

    def __init__(self):
        super().__init__()
        # Use port from config (SBS BaseStation output, default 30003)
        self.port = config.DUMP1090_PORT

        # Live tracking state
        self.aircraft_dict: dict = {}
        self._signal_events: list = []
        self._noise_history: deque = deque(maxlen=_NOISE_HISTORY_MAXLEN)

        # Antenna mode (from config default)
        self._antenna_mode = config.DEFAULT_ANTENNA_MODE

        # Background task handle
        self._tracking_task: asyncio.Task | None = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # Antenna mode control
    # ------------------------------------------------------------------

    def set_antenna_mode(self, mode) -> None:
        """Set the active antenna profile."""
        self._antenna_mode = mode
        profile = config.ANTENNA_PROFILES.get(mode, {})
        logger.info(
            f"Antenna mode set to {mode}: {profile.get('description', '')}"
        )

    # ------------------------------------------------------------------
    # Background tracking lifecycle
    # ------------------------------------------------------------------

    async def start_tracking(self):
        """Start the background SDR read loop (coroutine for asyncio.create_task)."""
        self._running = True
        await self._read_loop()

    async def stop_tracking(self):
        """Signal the background loop to stop and wait for it to finish."""
        self._running = False
        if self._tracking_task and not self._tracking_task.done():
            self._tracking_task.cancel()
            try:
                await self._tracking_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Internal background loop
    # ------------------------------------------------------------------

    async def _read_loop(self):
        """Continuously connect to dump1090 and ingest SBS messages."""
        while self._running:
            try:
                connected = await self.connect()
                if not connected:
                    logger.warning("Could not connect to dump1090. Retrying in 5 s…")
                    await asyncio.sleep(5)
                    continue
                async for msg in self.read_messages():
                    if not self._running:
                        break
                    if msg:
                        self._update_aircraft(msg)
                        self._record_signal_event(msg)
                        self._record_noise(msg)
                # Connection dropped — reset flag and retry
                self.connected = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"SDR read loop error: {e}. Retrying in 5 s…")
                self.connected = False
            if self._running:
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

    def _record_noise(self, msg: dict):
        """Record a noise floor sample based on current RSSI values."""
        rssi_values = [
            ac.get("rssi")
            for ac in self.aircraft_dict.values()
            if ac.get("rssi") is not None
        ]
        if rssi_values:
            noise_dbm = round(sum(rssi_values) / len(rssi_values), 1)
            self._noise_history.append({
                "noise_dbm": noise_dbm,
                "timestamp": datetime.utcnow().isoformat(),
            })

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

        noise_history_list = list(self._noise_history)

        return {
            "signal_type": signal_type,
            "confidence": confidence,
            "noise_dbm": avg_rssi,
            "adsb_dbm": avg_rssi,
            "aircraft_count": len(self.aircraft_dict),
            "noise_history": noise_history_list,
            "adsb_history": noise_history_list,
            "raw_messages": [],
        }

    async def get_signal_events(self) -> list:
        """Return the list of recent signal events (newest last)."""
        return list(self._signal_events)

