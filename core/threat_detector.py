"""
OPHIR Threat Detector Module
Detects anomalies in RF / ADS-B signal streams.
"""

import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_detector_instance = None


class ThreatDetector:
    """Simple threshold-based anomaly detector."""

    NOISE_ANOMALY_THRESHOLD = -20.0  # dBm – unusually strong signal

    def __init__(self, history_len: int = 10000):
        self.noise_history: deque = deque(maxlen=history_len)
        logger.info("✅ Threat Detector INITIALIZED")

    def record(self, noise_dbm: float, signal_type: str = "UNKNOWN"):
        """Add a new data point to the history."""
        self.noise_history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "noise_dbm": noise_dbm,
                "signal_type": signal_type,
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
