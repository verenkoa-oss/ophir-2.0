"""
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
