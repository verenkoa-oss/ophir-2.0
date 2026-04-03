"""
OPHIR SDR Real Module
Extends SDRReader with live aircraft tracking state,
noise data and signal event streaming.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
import random
from datetime import datetime
from core.sdr import SDRReader as _BaseSDRReader
import config

logger = logging.getLogger(__name__)

# dBm above rssi_threshold that counts as "strong" signal
_STRONG_SIGNAL_OFFSET_DB = 30


class SDRReader(_BaseSDRReader):
    """SDRReader with per-aircraft state tracking and helper data methods."""

    def __init__(self):
        super().__init__()
        # hex_code -> latest aircraft data dict
        self.aircraft_dict: dict = {}
        self._signal_events: list = []
        self._noise_history: list = []
        self._running = False
        self._task = None
        # Antenna profile – updated at runtime via set_antenna_mode()
        self._antenna_mode: config.AntennaMode = config.DEFAULT_ANTENNA_MODE
        self._antenna_profile: dict = config.ANTENNA_PROFILES[self._antenna_mode]

    # ------------------------------------------------------------------
    # Antenna mode management
    # ------------------------------------------------------------------

    def set_antenna_mode(self, mode: config.AntennaMode) -> None:
        """Switch the active antenna profile (no restart required)."""
        self._antenna_mode = mode
        self._antenna_profile = config.ANTENNA_PROFILES[mode]
        logger.info(
            f"📡 Antenna mode switched to {mode.value} "
            f"(rssi_threshold={self._antenna_profile['rssi_threshold']}, "
            f"gain={self._antenna_profile['gain']})"
        )

    @property
    def rssi_threshold(self) -> int:
        return self._antenna_profile["rssi_threshold"]

    # ------------------------------------------------------------------
    # Background tracking loop
    # ------------------------------------------------------------------

    async def start_tracking(self):
        """Start background task that reads dump1090 and updates aircraft_dict."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tracking_loop())
        logger.info("✅ Aircraft tracking loop started")

    async def stop_tracking(self):
        """Stop background tracking task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Aircraft tracking loop stopped")

    async def _tracking_loop(self):
        """Continuously read messages and maintain aircraft_dict."""
        while self._running:
            try:
                if not self.connected:
                    try:
                        await self.connect()
                    except Exception as e:
                        logger.warning(f"dump1090 not available: {e}. Retrying in 5s.")
                        await asyncio.sleep(5)
                        continue

                async for msg in self.read_messages():
                    if not self._running:
                        break
                    if msg and msg.get("hex_code"):
                        hex_code = msg["hex_code"]
                        existing = self.aircraft_dict.get(hex_code, {})
                        # Merge: keep existing fields, overwrite with new non-None values
                        new_fields = {k: v for k, v in msg.items() if v is not None}
                        merged = {**existing, **new_fields}
                        merged["last_seen"] = datetime.utcnow().isoformat()
                        if "first_seen" not in merged:
                            merged["first_seen"] = merged["last_seen"]
                        self.aircraft_dict[hex_code] = merged

                        # Record a lightweight signal event
                        event = {
                            "hex_code": hex_code,
                            "callsign": merged.get("callsign"),
                            "rssi": merged.get("rssi"),
                            "timestamp": merged["last_seen"],
                        }
                        self._signal_events.append(event)
                        if len(self._signal_events) > 500:
                            self._signal_events = self._signal_events[-500:]

                        # Noise sample from RSSI
                        rssi = merged.get("rssi")
                        if rssi is not None:
                            self._noise_history.append(
                                {"noise_dbm": rssi, "timestamp": merged["last_seen"]}
                            )
                            if len(self._noise_history) > 200:
                                self._noise_history = self._noise_history[-200:]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Tracking loop error: {e}")
                self.connected = False
                await asyncio.sleep(3)

    # ------------------------------------------------------------------
    # Public async helpers used by main.py
    # ------------------------------------------------------------------

    async def get_noise_data(self) -> dict:
        """Return a snapshot of the current RF environment."""
        if not self._noise_history:
            return {
                "signal_type": "NO_SIGNAL",
                "confidence": 0,
                "noise_dbm": 0,
                "antenna_mode": self._antenna_mode.value,
            }

        recent = self._noise_history[-20:]
        avg_rssi = sum(r["noise_dbm"] for r in recent) / len(recent)

        # Use antenna-profile threshold to classify signal strength
        threshold = self.rssi_threshold  # e.g. -75 GARAGE, -90 AIR
        strong_thresh = threshold + _STRONG_SIGNAL_OFFSET_DB

        if avg_rssi >= strong_thresh:
            signal_type = "STRONG"
            confidence = 0.9
        elif avg_rssi >= threshold:
            signal_type = "NORMAL"
            confidence = 0.75
        else:
            signal_type = "WEAK"
            confidence = 0.5

        return {
            "signal_type": signal_type,
            "confidence": confidence,
            "noise_dbm": round(avg_rssi, 2),
            "samples": len(recent),
            "antenna_mode": self._antenna_mode.value,
        }

    async def get_signal_events(self) -> list:
        """Return recent signal events."""
        return list(self._signal_events)

    def get_last_signal_timestamp(self) -> str | None:
        """Return the timestamp of the most recent signal event, or None."""
        if self._signal_events:
            return self._signal_events[-1].get("timestamp")
        return None
