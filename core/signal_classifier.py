"""
OPHIR Signal Classifier Module
Classifies received radio signals by type (ADS-B, Mode-S, civilian, military),
calculates RSSI statistics and SNR, and exposes a get_classifier() factory.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime, timezone
from collections import deque

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ICAO hex-prefix ranges known to belong to military registries.
# These are approximate; the full database would be loaded from a JSON file.
# ---------------------------------------------------------------------------
_MILITARY_PREFIXES = {
    # United States military (AE0000–AFFFFF)
    "AE", "AF",
    # UK military (43C000–43CFFF, 43F000–43FFFF)
    "43C", "43F",
    # France military (3A0000–3AFFFF partial)
    "3A",
}

# RSSI thresholds (dBm)
_SHADOW_RSSI = -90   # signals weaker than this are flagged as shadow / noise
_STRONG_RSSI = -50   # strong, reliable signal


class SignalClassifier:
    """
    Classifies ADS-B / Mode-S signals by type.

    Methods
    -------
    classify(hex_code, rssi, altitude, callsign) -> dict
        Return classification result for a single signal observation.
    get_stats() -> dict
        Return aggregate statistics over all classified signals.
    """

    def __init__(self):
        self._history: deque = deque(maxlen=1000)
        self._type_counts: dict = {
            "ADS-B": 0,
            "Mode-S": 0,
            "civilian": 0,
            "military": 0,
            "shadow": 0,
            "noise": 0,
        }
        logger.info("✅ SignalClassifier initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self,
        hex_code: str,
        rssi: float | None = None,
        altitude: float | None = None,
        callsign: str | None = None,
        has_position: bool = False,
    ) -> dict:
        """
        Classify a single signal observation.

        Parameters
        ----------
        hex_code  : ICAO 24-bit address (6 hex chars)
        rssi      : Received signal strength in dBm (optional)
        altitude  : Altitude in feet (optional)
        callsign  : Flight callsign (optional)
        has_position : True if lat/lon were present in the message

        Returns
        -------
        dict with keys: signal_type, aircraft_class, confidence, rssi_class, snr
        """
        signal_type = self._detect_signal_type(hex_code, altitude, has_position)
        aircraft_class = self._detect_aircraft_class(hex_code, callsign)
        rssi_class = self._classify_rssi(rssi)
        snr = self._estimate_snr(rssi)
        confidence = self._compute_confidence(rssi, has_position, signal_type)

        result = {
            "hex_code": hex_code,
            "signal_type": signal_type,
            "aircraft_class": aircraft_class,
            "rssi_class": rssi_class,
            "rssi": rssi,
            "snr": snr,
            "confidence": confidence,
            "has_position": has_position,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Update counters
        self._type_counts[signal_type] = self._type_counts.get(signal_type, 0) + 1
        self._type_counts[aircraft_class] = self._type_counts.get(aircraft_class, 0) + 1
        self._history.append(result)

        return result

    def get_stats(self) -> dict:
        """Return aggregate classification statistics."""
        total = sum(1 for _ in self._history)
        return {
            "total_classified": total,
            "type_counts": dict(self._type_counts),
            "history_size": len(self._history),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_signal_type(
        self, hex_code: str, altitude: float | None, has_position: bool
    ) -> str:
        """Determine raw signal modulation type."""
        if altitude is not None and has_position:
            return "ADS-B"
        if altitude is not None:
            return "Mode-S"
        return "shadow"

    def _detect_aircraft_class(self, hex_code: str, callsign: str | None) -> str:
        """Determine civilian vs military based on ICAO prefix."""
        hex_upper = (hex_code or "").upper()
        # Check 3-char prefix first, then 2-char
        for prefix in [hex_upper[:3], hex_upper[:2]]:
            if prefix in _MILITARY_PREFIXES:
                return "military"
        # Heuristic: callsigns starting with digits are often military
        if callsign and callsign[:1].isdigit():
            return "military"
        return "civilian"

    def _classify_rssi(self, rssi: float | None) -> str:
        """Categorise RSSI level."""
        if rssi is None:
            return "unknown"
        if rssi < _SHADOW_RSSI:
            return "shadow"
        if rssi < -70:
            return "weak"
        if rssi < _STRONG_RSSI:
            return "moderate"
        return "strong"

    def _estimate_snr(self, rssi: float | None) -> float:
        """Rough SNR estimate (dB) based on typical receiver noise floor."""
        if rssi is None:
            return 0.0
        noise_floor = -110.0  # typical SDR noise floor (dBm)
        return round(rssi - noise_floor, 1)

    def _compute_confidence(
        self, rssi: float | None, has_position: bool, signal_type: str
    ) -> int:
        """Return classification confidence as 0-100 integer."""
        score = 50  # base
        if has_position:
            score += 20
        if rssi is not None:
            if rssi >= _STRONG_RSSI:
                score += 20
            elif rssi >= -70:
                score += 10
            elif rssi < _SHADOW_RSSI:
                score -= 20
        if signal_type == "ADS-B":
            score += 10
        return max(0, min(100, score))


# Module-level singleton
_classifier_instance: SignalClassifier | None = None


def get_classifier() -> SignalClassifier:
    """Factory: return the module-level SignalClassifier singleton."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = SignalClassifier()
    return _classifier_instance
