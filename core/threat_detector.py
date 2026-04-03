"""
OPHIR Threat Detector
Detects anomalies in aircraft behaviour: unusual speed/altitude changes,
shadow targets (very low RSSI), and statistical noise spikes.
"""

import logging
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)

# Thresholds
_MAX_ALTITUDE_CHANGE_FT = 5_000    # ft per update
_MAX_SPEED_CHANGE_KT = 150         # kt per update
_SHADOW_RSSI_THRESHOLD = -90       # dBm
_NOISE_SPIKE_THRESHOLD = 20        # dBm jump considered a spike
_HISTORY_SIZE = 200


class ThreatDetector:
    """
    Detects anomalies in aggregated aircraft and noise data.

    Usage
    -----
    detector = get_detector()
    result = detector.detect_anomaly(aircraft_data)
    if result:
        print(result["type"], result["reason"])
    """

    def __init__(self):
        # Rolling noise history: list of {"noise_dbm": float, "timestamp": str}
        self.noise_history: list = []
        # Per-aircraft previous state: hex_code -> {"altitude": float, "speed": float}
        self._prev_state: dict = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_anomaly(self, aircraft_data: dict | None = None) -> dict | None:
        """
        Analyse aircraft_data (or just noise history) for anomalies.

        Returns a dict describing the anomaly, or None if everything is normal.
        """
        if aircraft_data:
            return self._check_aircraft(aircraft_data)
        return self._check_noise()

    def record_noise(self, noise_dbm: float):
        """Feed a noise sample into history."""
        self.noise_history.append(
            {"noise_dbm": noise_dbm, "timestamp": datetime.utcnow().isoformat()}
        )
        if len(self.noise_history) > _HISTORY_SIZE:
            self.noise_history = self.noise_history[-_HISTORY_SIZE:]

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _check_aircraft(self, data: dict) -> dict | None:
        hex_code = (data.get("hex_code") or "").upper()
        altitude = data.get("altitude")
        speed = data.get("ground_speed")
        rssi = data.get("rssi")

        # Shadow target check
        if rssi is not None and rssi < _SHADOW_RSSI_THRESHOLD:
            return {
                "type": "SHADOW_TARGET",
                "hex_code": hex_code,
                "reason": f"Very low RSSI {rssi:.1f} dBm – possible shadow/relay",
                "severity": "HIGH",
                "timestamp": datetime.utcnow().isoformat(),
            }

        prev = self._prev_state.get(hex_code, {})

        # Unusual altitude change
        if altitude is not None and prev.get("altitude") is not None:
            delta = abs(altitude - prev["altitude"])
            if delta > _MAX_ALTITUDE_CHANGE_FT:
                self._update_state(hex_code, altitude, speed)
                return {
                    "type": "UNUSUAL_ALTITUDE_CHANGE",
                    "hex_code": hex_code,
                    "reason": f"Altitude changed by {delta:.0f} ft in one update",
                    "severity": "MEDIUM",
                    "timestamp": datetime.utcnow().isoformat(),
                }

        # Unusual speed change
        if speed is not None and prev.get("speed") is not None:
            delta = abs(speed - prev["speed"])
            if delta > _MAX_SPEED_CHANGE_KT:
                self._update_state(hex_code, altitude, speed)
                return {
                    "type": "UNUSUAL_SPEED_CHANGE",
                    "hex_code": hex_code,
                    "reason": f"Speed changed by {delta:.0f} kt in one update",
                    "severity": "MEDIUM",
                    "timestamp": datetime.utcnow().isoformat(),
                }

        self._update_state(hex_code, altitude, speed)
        return None

    def _check_noise(self) -> dict | None:
        """Look for a noise spike in the recent history."""
        if len(self.noise_history) < 2:
            return None
        recent = self.noise_history[-10:]
        values = [r["noise_dbm"] for r in recent if r["noise_dbm"] is not None]
        if len(values) < 2:
            return None
        spike = max(values) - min(values)
        if spike >= _NOISE_SPIKE_THRESHOLD:
            return {
                "type": "NOISE_SPIKE",
                "reason": f"RF noise swing of {spike:.1f} dBm detected",
                "severity": "LOW",
                "timestamp": datetime.utcnow().isoformat(),
            }
        return None

    def _update_state(self, hex_code: str, altitude, speed):
        self._prev_state[hex_code] = {"altitude": altitude, "speed": speed}


# Module-level singleton
_detector_instance: ThreatDetector | None = None


def get_detector() -> ThreatDetector:
    """Return the shared ThreatDetector instance (creates on first call)."""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = ThreatDetector()
        logger.info("✅ ThreatDetector initialized")
    return _detector_instance
