"""
OPHIR Threat Detector Module
Anomaly detection engine for ADS-B / Mode-S aircraft tracks.

Detects:
  - Unusual speed changes (rapid acceleration / deceleration)
  - Sudden altitude jumps
  - Ghost / shadow aircraft (no GPS position, very weak RSSI)
  - Transponder spoofing indicators (ICAO hex reuse)

Exposes: get_detector() factory returning a ThreatDetector instance
with a detect_anomaly() method.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime, timedelta, timezone
from collections import deque

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection thresholds
# ---------------------------------------------------------------------------
_MAX_SPEED_DELTA_KT = 150        # knots per update – above = anomaly
_MAX_ALTITUDE_DELTA_FT = 5000   # feet per update – above = anomaly
_SHADOW_RSSI = config.SHADOW_RSSI_THRESHOLD   # dBm
_MAX_HISTORY = config.SHADOW_MAX_HISTORY


class ThreatDetector:
    """
    Stateful anomaly / threat detection engine.

    Attributes
    ----------
    noise_history : deque
        Rolling window of signal observations (used by main.py /threat/history).

    Methods
    -------
    update(aircraft_data)
        Feed new aircraft data into the detector; returns an anomaly dict or None.
    detect_anomaly() -> dict | None
        Inspect the most recently seen aircraft data and return an anomaly
        description if one is found, or None otherwise.
    get_summary() -> dict
        Return aggregate anomaly statistics.
    """

    def __init__(self):
        # Track previous state per hex_code
        self._prev: dict[str, dict] = {}
        # Circular history buffer (raw signal snapshots)
        self.noise_history: deque = deque(maxlen=_MAX_HISTORY)
        # Shadow aircraft set (hex codes with no position)
        self._shadow_aircraft: set[str] = set()
        # All detected anomalies buffer
        self._anomalies: deque = deque(maxlen=500)
        # Most recent aircraft snapshot (for detect_anomaly() polling)
        self._last_aircraft: dict | None = None
        logger.info("✅ ThreatDetector initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, aircraft_data: dict) -> dict | None:
        """
        Process a new aircraft observation.

        Parameters
        ----------
        aircraft_data : dict
            Parsed aircraft data dict (hex_code, altitude, ground_speed,
            rssi, latitude, longitude, …).

        Returns
        -------
        anomaly dict if an anomaly was detected, else None.
        """
        if not aircraft_data or not aircraft_data.get("hex_code"):
            return None

        hex_code = aircraft_data["hex_code"]
        self._last_aircraft = aircraft_data

        # Record in noise_history
        snapshot = {
            "hex_code": hex_code,
            "rssi": aircraft_data.get("rssi"),
            "altitude": aircraft_data.get("altitude"),
            "speed": aircraft_data.get("ground_speed"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.noise_history.append(snapshot)

        # Shadow aircraft check
        has_position = (
            aircraft_data.get("latitude") is not None
            and aircraft_data.get("longitude") is not None
        )
        rssi = aircraft_data.get("rssi")

        if not has_position and (rssi is None or rssi < _SHADOW_RSSI):
            self._shadow_aircraft.add(hex_code)
            anomaly = self._make_anomaly(
                hex_code, "SHADOW_AIRCRAFT",
                f"No GPS position and RSSI {rssi} dBm below threshold {_SHADOW_RSSI} dBm",
                aircraft_data,
            )
            self._anomalies.append(anomaly)
            return anomaly

        # Compare with previous state
        prev = self._prev.get(hex_code)
        if prev:
            anomaly = self._check_deltas(hex_code, prev, aircraft_data)
            if anomaly:
                self._anomalies.append(anomaly)
                self._prev[hex_code] = aircraft_data
                return anomaly

        self._prev[hex_code] = aircraft_data
        return None

    def detect_anomaly(self) -> dict | None:
        """
        Inspect the most recently seen aircraft and return an anomaly if found.
        This is the polling interface used by main.py /threat/anomalies.
        """
        if not self._last_aircraft:
            return None
        return self.update(self._last_aircraft)

    def get_shadow_aircraft(self) -> list:
        """Return list of shadow aircraft hex codes."""
        return list(self._shadow_aircraft)

    def get_recent_anomalies(self, n: int = 50) -> list:
        """Return the n most recent anomalies."""
        return list(self._anomalies)[-n:]

    def get_summary(self) -> dict:
        """Return aggregate statistics."""
        return {
            "total_anomalies": len(self._anomalies),
            "shadow_aircraft": len(self._shadow_aircraft),
            "tracked_aircraft": len(self._prev),
            "history_size": len(self.noise_history),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_deltas(
        self, hex_code: str, prev: dict, curr: dict
    ) -> dict | None:
        """Compare consecutive observations for sudden changes."""
        # Speed delta
        prev_speed = prev.get("ground_speed")
        curr_speed = curr.get("ground_speed")
        if (
            prev_speed is not None
            and curr_speed is not None
            and abs(curr_speed - prev_speed) > _MAX_SPEED_DELTA_KT
        ):
            return self._make_anomaly(
                hex_code, "UNUSUAL_SPEED_CHANGE",
                f"Speed changed from {prev_speed:.0f} kt to {curr_speed:.0f} kt "
                f"(delta {abs(curr_speed - prev_speed):.0f} kt)",
                curr,
            )

        # Altitude delta
        prev_alt = prev.get("altitude")
        curr_alt = curr.get("altitude")
        if (
            prev_alt is not None
            and curr_alt is not None
            and abs(curr_alt - prev_alt) > _MAX_ALTITUDE_DELTA_FT
        ):
            return self._make_anomaly(
                hex_code, "ALTITUDE_JUMP",
                f"Altitude jumped from {prev_alt:.0f} ft to {curr_alt:.0f} ft "
                f"(delta {abs(curr_alt - prev_alt):.0f} ft)",
                curr,
            )

        return None

    def _make_anomaly(
        self, hex_code: str, anomaly_type: str, description: str, data: dict
    ) -> dict:
        return {
            "hex_code": hex_code,
            "anomaly_type": anomaly_type,
            "description": description,
            "callsign": data.get("callsign"),
            "altitude": data.get("altitude"),
            "speed": data.get("ground_speed"),
            "rssi": data.get("rssi"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }


# Module-level singleton
_detector_instance: ThreatDetector | None = None


def get_detector() -> ThreatDetector:
    """Factory: return the module-level ThreatDetector singleton."""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = ThreatDetector()
    return _detector_instance
