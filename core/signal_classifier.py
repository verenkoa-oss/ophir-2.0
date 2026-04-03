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
OPHIR Signal Classifier
Rule-based signal classification for ADS-B aircraft messages.
Classifies signals as military, civilian, or anomaly.
"""

import logging
import re

logger = logging.getLogger(__name__)

# ICAO hex prefix ranges used by military/government operators
# (approximate; based on ICAO country allocation blocks)
_MILITARY_HEX_PREFIXES = (
    "AE",   # US military (USAF, USN, etc.)
    "43C",  # French military
    "3B",   # German military
    "7F",   # Unallocated / test
)

# Callsign patterns that suggest military or government use
_MILITARY_CALLSIGN_PATTERNS = re.compile(
    r"^(DUKE|REACH|LAGR|JAKE|IRON|COBRA|EAGLE|VIPER|RAZOR|GHOST|RCH|SAM|SUI|"
    r"MAGMA|ARMY|NAVY|USAF|NATO|RRR|RFF|CTM|CTF|AME)\d*",
    re.IGNORECASE,
)

# Anomaly thresholds
_ANOMALY_ALTITUDE_FT = 60_000   # above 60,000 ft is unusual for civil traffic
_ANOMALY_SPEED_KT = 700         # above 700 kt ground speed is unusual


class SignalClassifier:
    """Classify a decoded ADS-B message into a category."""

    CATEGORY_MILITARY = "MILITARY"
    CATEGORY_CIVILIAN = "CIVILIAN"
    CATEGORY_ANOMALY = "ANOMALY"
    CATEGORY_UNKNOWN = "UNKNOWN"

    def classify(self, signal_data: dict) -> dict:
        """
        Classify a single aircraft message.

        Parameters
        ----------
        signal_data : dict
            Decoded ADS-B/SBS record with keys such as hex_code, callsign,
            altitude, ground_speed, rssi, etc.

        Returns
        -------
        dict
            {
                "category": str,          # MILITARY / CIVILIAN / ANOMALY / UNKNOWN
                "confidence": float,      # 0.0–1.0
                "reason": str,            # human-readable explanation
            }
        """
        if not signal_data:
            return {"category": self.CATEGORY_UNKNOWN, "confidence": 0.0, "reason": "No data"}

        hex_code = (signal_data.get("hex_code") or "").upper()
        callsign = (signal_data.get("callsign") or "").strip().upper()
        altitude = signal_data.get("altitude")
        speed = signal_data.get("ground_speed")

        # ---- Anomaly checks (highest priority) ----
        if altitude is not None and altitude > _ANOMALY_ALTITUDE_FT:
            return {
                "category": self.CATEGORY_ANOMALY,
                "confidence": 0.95,
                "reason": f"Extreme altitude {altitude:.0f} ft",
            }
        if speed is not None and speed > _ANOMALY_SPEED_KT:
            return {
                "category": self.CATEGORY_ANOMALY,
                "confidence": 0.90,
                "reason": f"Extreme speed {speed:.0f} kt",
            }

        # ---- Military checks ----
        if callsign and _MILITARY_CALLSIGN_PATTERNS.match(callsign):
            return {
                "category": self.CATEGORY_MILITARY,
                "confidence": 0.85,
                "reason": f"Military callsign pattern: {callsign}",
            }
        if hex_code and any(hex_code.startswith(pfx) for pfx in _MILITARY_HEX_PREFIXES):
            return {
                "category": self.CATEGORY_MILITARY,
                "confidence": 0.75,
                "reason": f"Military ICAO block: {hex_code[:3]}",
            }

        # ---- Civilian / unknown ----
        if hex_code:
            return {
                "category": self.CATEGORY_CIVILIAN,
                "confidence": 0.70,
                "reason": "Civilian ICAO allocation",
            }

        return {"category": self.CATEGORY_UNKNOWN, "confidence": 0.0, "reason": "Insufficient data"}


# Module-level singleton
_classifier_instance: SignalClassifier | None = None


def get_classifier() -> SignalClassifier:
    """Return the shared SignalClassifier instance (creates on first call)."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = SignalClassifier()
        logger.info("✅ SignalClassifier initialized")
    return _classifier_instance
