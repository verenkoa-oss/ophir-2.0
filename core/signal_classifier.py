"""
OPHIR Signal Classifier Module
Classifies ADS-B signals and aircraft behaviour patterns.
The current implementation is a rule-based stub; replace or extend the
SignalClassifier.classify() method with an ML model as the project matures.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class SignalClassifier:
    """
    Rule-based signal / aircraft classifier.

    classify(aircraft_data) returns a dict with:
        - category   : str  (e.g. 'MILITARY', 'COMMERCIAL', 'GENERAL_AVIATION', 'UNKNOWN')
        - confidence : float  0.0 – 1.0
        - flags      : list[str]  (any special observations)
    """

    # Known military hex-code prefixes (ICAO blocks allocated to military)
    _MILITARY_PREFIXES = (
        "AE",   # US military (DoD)
        "A9",   # various US government
    )

    # Callsign patterns typical of military / special operations
    _MILITARY_CALLSIGNS = (
        "RCH",   # USAF Air Mobility Command
        "MOOSE",
        "REACH",
        "FORTE",
        "JAKE",
        "ROCKY",
        "DUKE",
        "IRON",
        "STEEL",
    )

    def classify(self, aircraft_data: dict) -> dict:
        """
        Classify a single aircraft based on available ADS-B data.

        Parameters
        ----------
        aircraft_data : dict
            Keys may include: hex_code, callsign, altitude, ground_speed,
            track, latitude, longitude, rssi, is_shadow, …

        Returns
        -------
        dict with keys: category, confidence, flags
        """
        hex_code = (aircraft_data.get("hex_code") or "").upper()
        callsign = (aircraft_data.get("callsign") or "").upper().strip()
        altitude = aircraft_data.get("altitude")
        ground_speed = aircraft_data.get("ground_speed")
        is_shadow = aircraft_data.get("is_shadow", False)

        flags: list = []
        category = "UNKNOWN"
        confidence = 0.5

        # ---- Shadow / no-position detection ----
        if is_shadow or (aircraft_data.get("latitude") is None and aircraft_data.get("longitude") is None):
            flags.append("NO_POSITION")

        # ---- Hex-prefix based military detection ----
        if any(hex_code.startswith(pfx) for pfx in self._MILITARY_PREFIXES):
            category = "MILITARY"
            confidence = 0.85
            flags.append("MILITARY_HEX_PREFIX")

        # ---- Callsign-based military detection ----
        if callsign and any(callsign.startswith(cs) for cs in self._MILITARY_CALLSIGNS):
            category = "MILITARY"
            confidence = max(confidence, 0.90)
            flags.append("MILITARY_CALLSIGN")

        # ---- Basic civilian airline detection ----
        if category == "UNKNOWN" and callsign and len(callsign) >= 3:
            # IATA/ICAO airline codes are typically 2–3 uppercase letters followed by digits
            prefix = "".join(c for c in callsign if c.isalpha())
            if 2 <= len(prefix) <= 3:
                category = "COMMERCIAL"
                confidence = 0.70

        # ---- Altitude / speed anomaly flags ----
        if altitude is not None:
            if altitude < 0:
                flags.append("NEGATIVE_ALTITUDE")
            elif altitude > 60000:
                flags.append("EXTREME_ALTITUDE")

        if ground_speed is not None:
            if ground_speed > 600:
                flags.append("HIGH_SPEED")
            elif ground_speed < 0:
                flags.append("INVALID_SPEED")

        logger.debug(
            f"Classified {hex_code} ({callsign}) → {category} "
            f"(confidence={confidence:.2f}, flags={flags})"
        )

        return {
            "category": category,
            "confidence": round(confidence, 2),
            "flags": flags,
            "classified_at": datetime.utcnow().isoformat(),
        }


def get_classifier() -> SignalClassifier:
    """
    Factory function — returns a ready-to-use SignalClassifier instance.
    Swap this out for a ML-model-backed classifier in future iterations.
    """
    logger.info("SignalClassifier initialised (rule-based stub)")
    return SignalClassifier()
