"""
OPHIR 2.0 | Learning Engine
Collects civilian aircraft data to build baseline patterns.
Detects anomalies by comparing unknown aircraft to the learned civilian baseline.
No simulation – only real ADS-B data is processed.
"""

import asyncio
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_BASELINE_FILE = Path(config.CACHE_DIR) / "civilian_baseline.json"
_MIN_SAMPLES_FOR_BASELINE = 5  # minimum civilian samples per category


class LearningEngine:
    """Learn normal flight patterns from civilian ADS-B traffic."""

    def __init__(self):
        self._civilian_samples: list[dict] = []
        # Per-type aggregates: type_str -> {alt_sum, speed_sum, count, rssi_sum}
        self._baselines: dict[str, dict] = defaultdict(lambda: {
            "alt_sum": 0.0, "speed_sum": 0.0, "rssi_sum": 0.0, "count": 0
        })
        self._load_baseline()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_baseline(self) -> None:
        try:
            if _BASELINE_FILE.exists():
                data = json.loads(_BASELINE_FILE.read_text())
                self._civilian_samples = data.get("samples", [])
                raw_bl = data.get("baselines", {})
                for k, v in raw_bl.items():
                    self._baselines[k] = v
                logger.info(f"✅ Loaded civilian baseline: {len(self._civilian_samples)} samples")
        except Exception as e:
            logger.warning(f"Could not load baseline: {e}")

    def _save_baseline(self) -> None:
        try:
            _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "samples": self._civilian_samples[-2000:],  # keep latest 2000
                "baselines": dict(self._baselines),
                "updated": datetime.now(timezone.utc).isoformat(),
            }
            _BASELINE_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"Could not save baseline: {e}")

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def learn_from_aircraft(self, aircraft: dict) -> None:
        """Ingest a civilian aircraft data point into the baseline.

        Only processes aircraft with GPS coordinates (real signal, no simulation).
        """
        lat = aircraft.get("latitude") or aircraft.get("lat")
        lon = aircraft.get("longitude") or aircraft.get("lon")
        if lat is None or lon is None:
            return  # require real coordinates

        ac_type = (aircraft.get("aircraft_type") or "commercial").lower()
        alt = aircraft.get("altitude")
        speed = aircraft.get("ground_speed") or aircraft.get("speed")
        rssi = aircraft.get("rssi")

        sample = {
            "hex": aircraft.get("hex_code") or aircraft.get("hex", ""),
            "callsign": aircraft.get("callsign", ""),
            "type": ac_type,
            "lat": float(lat),
            "lon": float(lon),
            "alt": float(alt) if alt is not None else None,
            "speed": float(speed) if speed is not None else None,
            "rssi": float(rssi) if rssi is not None else None,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._civilian_samples.append(sample)

        bl = self._baselines[ac_type]
        if alt is not None:
            bl["alt_sum"] += float(alt)
        if speed is not None:
            bl["speed_sum"] += float(speed)
        if rssi is not None:
            bl["rssi_sum"] += float(rssi)
        bl["count"] += 1

        # Persist every 50 new samples
        if len(self._civilian_samples) % 50 == 0:
            self._save_baseline()

    # ------------------------------------------------------------------
    # Anomaly scoring
    # ------------------------------------------------------------------

    def anomaly_score(self, aircraft: dict) -> dict:
        """Return an anomaly score 0.0 (normal) … 1.0 (very anomalous).

        Compares aircraft metrics to the civilian baseline for its type.
        """
        ac_type = (aircraft.get("aircraft_type") or "commercial").lower()
        bl = self._baselines.get(ac_type)

        if bl is None or bl["count"] < _MIN_SAMPLES_FOR_BASELINE:
            return {"score": 0.5, "reason": "insufficient_baseline", "type": ac_type}

        reasons = []
        deviations = []

        alt = aircraft.get("altitude")
        speed = aircraft.get("ground_speed") or aircraft.get("speed")
        rssi = aircraft.get("rssi")

        n = bl["count"]
        if n > 0:
            avg_alt = bl["alt_sum"] / n
            avg_speed = bl["speed_sum"] / n
            avg_rssi = bl["rssi_sum"] / n if bl["rssi_sum"] else None

            if alt is not None and avg_alt > 0:
                dev = abs(float(alt) - avg_alt) / max(avg_alt, 1)
                deviations.append(dev)
                if dev > 0.5:
                    reasons.append(f"altitude_deviation_{dev:.0%}")

            if speed is not None and avg_speed > 0:
                dev = abs(float(speed) - avg_speed) / max(avg_speed, 1)
                deviations.append(dev)
                if dev > 0.4:
                    reasons.append(f"speed_deviation_{dev:.0%}")

            if rssi is not None and avg_rssi is not None:
                dev = abs(float(rssi) - avg_rssi) / max(abs(avg_rssi), 1)
                deviations.append(dev)
                if dev > 0.3:
                    reasons.append(f"rssi_deviation_{dev:.0%}")

        score = min(1.0, sum(deviations) / max(len(deviations), 1)) if deviations else 0.0
        # Additional bump for missing GPS (shadow target)
        if aircraft.get("latitude") is None:
            score = min(1.0, score + 0.2)
            reasons.append("no_gps")

        return {
            "score": round(score, 3),
            "reason": "; ".join(reasons) if reasons else "normal",
            "type": ac_type,
            "baseline_samples": n,
        }

    def get_stats(self) -> dict:
        """Return summary statistics of the learning engine."""
        return {
            "total_civilian_samples": len(self._civilian_samples),
            "aircraft_types_learned": len(self._baselines),
            "baselines": {
                t: {
                    "count": bl["count"],
                    "avg_altitude": round(bl["alt_sum"] / bl["count"], 0) if bl["count"] else None,
                    "avg_speed": round(bl["speed_sum"] / bl["count"], 1) if bl["count"] else None,
                }
                for t, bl in self._baselines.items()
                if bl["count"] > 0
            },
        }


# Module-level singleton
_engine: LearningEngine | None = None


def get_learning_engine() -> LearningEngine:
    global _engine
    if _engine is None:
        _engine = LearningEngine()
    return _engine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = get_learning_engine()

    # Feed some mock civilian samples (purely for self-test, not simulation)
    for i in range(10):
        engine.learn_from_aircraft({
            "hex_code": f"7C{i:04X}",
            "callsign": f"QF{100+i}",
            "aircraft_type": "commercial",
            "latitude": 31.0 + i * 0.01,
            "longitude": 35.0 + i * 0.01,
            "altitude": 35000 + i * 100,
            "ground_speed": 450 + i * 5,
            "rssi": -65 - i,
        })

    score = engine.anomaly_score({
        "aircraft_type": "commercial",
        "altitude": 1000,
        "ground_speed": 800,
        "rssi": -40,
    })
    print(f"Anomaly score for unusual aircraft: {score}")
    print(f"Stats: {engine.get_stats()}")
