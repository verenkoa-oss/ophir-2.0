"""
OPHIR Threat Detector Module
Detects anomalies in RF / ADS-B signal streams.
"""

import logging
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)


class ThreatDetector:
    """Detect threats from aircraft and RF signal patterns."""

    def __init__(self):
        self.noise_history: deque = deque(maxlen=1000)
        self.threat_log: list = []

    def update(self, noise_dbm: float, aircraft_count: int = 0):
        """Record a new data point."""
        self.noise_history.append(
            {
                "time": datetime.utcnow().isoformat(),
                "noise_dbm": noise_dbm,
                "aircraft_count": aircraft_count,
            }
        )

    def detect_anomaly(self) -> dict | None:
        """Return an anomaly dict if one is detected, otherwise None."""
        if not self.noise_history:
            return None

        latest = self.noise_history[-1]
        if latest["noise_dbm"] > self.NOISE_ANOMALY_THRESHOLD:
            return {
                "type": "HIGH_POWER_SIGNAL",
                "noise_dbm": latest["noise_dbm"],
                "timestamp": latest["timestamp"],
            }
        return None


def get_detector() -> ThreatDetector:
    """Return a singleton ThreatDetector instance."""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = ThreatDetector()
    return _detector_instance
