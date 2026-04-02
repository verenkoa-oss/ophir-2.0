"""
OPHIR 2.0 | Signal Classifier
Pattern-based AI signal classification for RF signals and ADS-B data.
"""

import logging
import math

logger = logging.getLogger(__name__)


class SignalClassifier:
    """Classify RF signals based on noise patterns and ADS-B activity."""

    def classify(self, noise_dbm: float, has_adsb: bool = False) -> dict:
        """Return signal classification for the current measurement."""
        if has_adsb:
            return {
                "type": "ADS-B",
                "confidence": 95,
                "description": "Aviation ADS-B transponder signal",
            }
        if noise_dbm > -75:
            return {
                "type": "RF_NOISE",
                "confidence": 70,
                "description": "Elevated RF noise floor detected",
            }
        if noise_dbm > -85:
            return {
                "type": "BACKGROUND",
                "confidence": 60,
                "description": "Background RF activity",
            }
        return {
            "type": "QUIET",
            "confidence": 80,
            "description": "Low noise floor — quiet spectrum",
        }

    def analyze_spectrum(self, noise_history: list) -> dict:
        """Analyze noise history list for statistical patterns."""
        if not noise_history:
            return {"pattern": "UNKNOWN", "anomaly": False, "avg_dbm": -95.0}

        levels = [n["level"] if isinstance(n, dict) else n for n in noise_history]
        avg = sum(levels) / len(levels)
        variance = sum((l - avg) ** 2 for l in levels) / len(levels)
        std_dev = math.sqrt(variance)

        if std_dev > 10:
            return {"pattern": "IRREGULAR", "anomaly": True, "avg_dbm": round(avg, 2)}
        if avg > -75:
            return {
                "pattern": "HIGH_ACTIVITY",
                "anomaly": False,
                "avg_dbm": round(avg, 2),
            }
        return {"pattern": "NORMAL", "anomaly": False, "avg_dbm": round(avg, 2)}


_classifier: SignalClassifier | None = None


def get_classifier() -> SignalClassifier:
    global _classifier
    if _classifier is None:
        _classifier = SignalClassifier()
        logger.info("✅ Signal classifier initialized")
    return _classifier
