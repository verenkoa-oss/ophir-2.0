"""
OPHIR 2.0 | Threat Detector
Detects anomalies in aircraft tracking and RF signal patterns.
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
        """Return an anomaly dict if one is detected, else None."""
        if len(self.noise_history) < 10:
            return None

        recent = list(self.noise_history)[-10:]
        levels = [r["noise_dbm"] for r in recent]
        avg = sum(levels) / len(levels)

        if avg > -65:
            return {
                "type": "HIGH_NOISE",
                "severity": "WARNING",
                "value": round(avg, 2),
                "message": f"Elevated noise floor: {avg:.1f} dBm",
            }
        return None


_detector: ThreatDetector | None = None


def get_detector() -> ThreatDetector:
    global _detector
    if _detector is None:
        _detector = ThreatDetector()
        logger.info("✅ Threat detector initialized")
    return _detector
