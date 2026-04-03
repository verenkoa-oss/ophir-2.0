"""
OPHIR Threat Detector Module
Detects anomalies and potential threats in ADS-B data.
The current implementation uses simple rule-based heuristics.
Replace or extend ThreatDetector with more sophisticated AI/ML logic as needed.
"""

import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Anomaly thresholds
_RSSI_SPIKE_THRESHOLD = 20.0      # dBm jump that triggers a spike alert
_ALTITUDE_DROP_THRESHOLD = 5000   # ft/update that triggers a rapid descent alert
_SPEED_CHANGE_THRESHOLD = 150     # kt/update that triggers an unusual speed change
_NOISE_HISTORY_MAXLEN = 500       # max entries kept in noise_history


class ThreatDetector:
    """
    Rule-based anomaly / threat detector.

    Maintains a rolling history of per-aircraft measurements and raises
    alerts when configurable thresholds are exceeded.

    noise_history : deque[dict]
        Ring-buffer of recent measurement snapshots — exposed so that
        GET /threat/history in main.py can iterate over it.

    detect_anomaly(aircraft_data=None) -> dict | None
        Returns a description of the most recently detected anomaly, or None.
        When aircraft_data is provided the snapshot is recorded first.
    """

    def __init__(self):
        # Rolling history of signal / aircraft snapshots (newest last)
        self.noise_history: deque = deque(maxlen=_NOISE_HISTORY_MAXLEN)

        # Per-aircraft state for delta-based anomaly detection
        self._prev_state: dict = {}   # hex_code -> {altitude, ground_speed, rssi}

        # Accumulated anomaly log (last 100)
        self._anomalies: deque = deque(maxlen=100)

        logger.info("ThreatDetector initialised")

    # ------------------------------------------------------------------
    # Public API used by main.py
    # ------------------------------------------------------------------

    def update(self, aircraft_data: dict):
        """
        Feed a new aircraft snapshot to the detector.
        Call this whenever the SDR reader updates an aircraft entry.

        Parameters
        ----------
        aircraft_data : dict
            Should contain at least: hex_code, altitude, ground_speed, rssi.
        """
        hex_code = aircraft_data.get("hex_code")
        if not hex_code:
            return

        snapshot = {
            "hex_code": hex_code,
            "callsign": aircraft_data.get("callsign"),
            "altitude": aircraft_data.get("altitude"),
            "ground_speed": aircraft_data.get("ground_speed"),
            "rssi": aircraft_data.get("rssi"),
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.noise_history.append(snapshot)

        # Delta-based anomaly checks
        if hex_code in self._prev_state:
            anomaly = self._check_deltas(hex_code, snapshot, self._prev_state[hex_code])
            if anomaly:
                self._anomalies.append(anomaly)

        self._prev_state[hex_code] = snapshot

    def detect_anomaly(self, aircraft_data: dict | None = None) -> dict | None:
        """
        Return the most recently detected anomaly, or None.

        If aircraft_data is provided, update internal state first, then check
        for anomalies (supports the calling convention detector.detect_anomaly(ac)).
        """
        if aircraft_data is not None:
            self.update(aircraft_data)

        # Return most recent accumulated anomaly first
        if self._anomalies:
            return dict(self._anomalies[-1])

        # Scan the most recent snapshot per aircraft for absolute thresholds
        for snapshot in reversed(list(self.noise_history)):
            anomaly = self._check_absolute(snapshot)
            if anomaly:
                return anomaly

        return None

    def get_all_anomalies(self) -> list:
        """Return all accumulated anomalies (newest last)."""
        return list(self._anomalies)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_deltas(self, hex_code: str, current: dict, previous: dict) -> dict | None:
        """Detect rapid changes between two consecutive snapshots."""
        alt_cur = current.get("altitude")
        alt_prev = previous.get("altitude")
        spd_cur = current.get("ground_speed")
        spd_prev = previous.get("ground_speed")
        rssi_cur = current.get("rssi")
        rssi_prev = previous.get("rssi")

        # Rapid altitude drop
        if alt_cur is not None and alt_prev is not None:
            if (alt_prev - alt_cur) > _ALTITUDE_DROP_THRESHOLD:
                return self._make_anomaly(
                    hex_code,
                    "RAPID_DESCENT",
                    f"Altitude dropped {alt_prev - alt_cur:.0f} ft in one update "
                    f"({alt_prev:.0f} → {alt_cur:.0f} ft)",
                    current,
                )

        # Unusual speed change
        if spd_cur is not None and spd_prev is not None:
            if abs(spd_cur - spd_prev) > _SPEED_CHANGE_THRESHOLD:
                return self._make_anomaly(
                    hex_code,
                    "UNUSUAL_SPEED_CHANGE",
                    f"Speed changed by {abs(spd_cur - spd_prev):.0f} kt "
                    f"({spd_prev:.0f} → {spd_cur:.0f} kt)",
                    current,
                )

        # RSSI spike (possible spoofing / interference)
        if rssi_cur is not None and rssi_prev is not None:
            if abs(rssi_cur - rssi_prev) > _RSSI_SPIKE_THRESHOLD:
                return self._make_anomaly(
                    hex_code,
                    "RSSI_SPIKE",
                    f"RSSI jumped by {abs(rssi_cur - rssi_prev):.1f} dBm "
                    f"({rssi_prev:.1f} → {rssi_cur:.1f} dBm)",
                    current,
                )

        return None

    def _check_absolute(self, snapshot: dict) -> dict | None:
        """Flag snapshots that exceed absolute (non-delta) thresholds."""
        altitude = snapshot.get("altitude")
        ground_speed = snapshot.get("ground_speed")
        hex_code = snapshot.get("hex_code", "UNKNOWN")

        if altitude is not None and altitude < 0:
            return self._make_anomaly(
                hex_code, "NEGATIVE_ALTITUDE",
                f"Aircraft reporting negative altitude: {altitude} ft",
                snapshot,
            )

        if ground_speed is not None and ground_speed > 700:
            return self._make_anomaly(
                hex_code, "EXTREME_SPEED",
                f"Aircraft reporting extreme speed: {ground_speed} kt",
                snapshot,
            )

        return None

    @staticmethod
    def _make_anomaly(hex_code: str, anomaly_type: str, description: str, data: dict) -> dict:
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
    """Return the module-level ThreatDetector singleton."""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = ThreatDetector()
    return _detector_instance
