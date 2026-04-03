"""
OPHIR Learning Engine
Collects real ADS-B data from civilian aircraft (those that broadcast GPS
coordinates) and uses it to build a local RSSI-vs-distance calibration model.
The calibrated model improves distance estimates for aircraft that do *not*
transmit GPS.

Data is persisted in a lightweight SQLite database (db/signal_training.db).
No synthetic data is ever inserted.
"""

import asyncio
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.distance_calculator import haversine_km, rssi_to_distance_km
import config

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "db" / "signal_training.db"

# Minimum number of calibration samples before the custom model is used
_MIN_SAMPLES_FOR_MODEL = 10
# Background recalibration interval (seconds)
_RECALIBRATION_INTERVAL_SECONDS = 300
# Maximum number of recent samples used when computing the calibration offset
_MAX_CALIBRATION_SAMPLES = 500


def _db_connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS training_samples (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            hex_code    TEXT    NOT NULL,
            callsign    TEXT,
            rssi_dbm    REAL    NOT NULL,
            distance_km REAL    NOT NULL,
            altitude_m  REAL,
            speed_kmh   REAL,
            latitude    REAL,
            longitude   REAL,
            recorded_at TEXT    NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_training_hex ON training_samples(hex_code)"
    )
    conn.commit()


class LearningEngine:
    """Collect civilian aircraft observations and calibrate the RSSI model."""

    def __init__(self) -> None:
        self._conn: Optional[sqlite3.Connection] = None
        # Calibration offset (dB) learned from civilian data; applied on top of
        # the Friis model to compensate for local antenna/environment factors.
        self._calibration_offset_db: float = 0.0
        self._sample_count: int = 0
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Open the SQLite database and load calibration state."""
        self._conn = _db_connect()
        _ensure_schema(self._conn)
        self._sample_count = self._count_samples()
        if self._sample_count >= _MIN_SAMPLES_FOR_MODEL:
            self._recalculate_offset()
        logger.info(
            f"✅ LearningEngine ready – {self._sample_count} training samples, "
            f"calibration_offset={self._calibration_offset_db:+.1f} dB"
        )

    async def start(self) -> None:
        """Start the background recalibration loop."""
        self.init()
        self._running = True
        self._task = asyncio.create_task(self._recalibration_loop())
        logger.info("✅ LearningEngine background loop started")

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            self._conn.close()
        logger.info("🛑 LearningEngine stopped")

    # ------------------------------------------------------------------
    # Training data ingestion
    # ------------------------------------------------------------------

    def record_civilian_observation(self, aircraft: dict) -> bool:
        """Record a single civilian aircraft observation.

        Only aircraft that broadcast GPS coordinates AND have a valid RSSI
        measurement are stored.  Returns True if the sample was stored.
        """
        rssi = aircraft.get("rssi")
        lat = aircraft.get("latitude")
        lon = aircraft.get("longitude")

        if rssi is None or lat is None or lon is None:
            return False

        distance_km = haversine_km(
            config.OBSERVER_LAT, config.OBSERVER_LON, lat, lon
        )
        if distance_km < config.DISTANCE_MIN_KM:
            return False  # Implausibly close – skip

        altitude_m = aircraft.get("altitude")
        if altitude_m:
            # Convert feet → metres
            altitude_m = altitude_m * 0.3048

        speed_mps = aircraft.get("ground_speed")
        speed_kmh = speed_mps * 1.852 if speed_mps else None

        if not self._conn:
            self.init()

        try:
            self._conn.execute(
                """
                INSERT INTO training_samples
                    (hex_code, callsign, rssi_dbm, distance_km,
                     altitude_m, speed_kmh, latitude, longitude, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aircraft.get("hex_code", "UNKNOWN"),
                    aircraft.get("callsign"),
                    float(rssi),
                    float(distance_km),
                    float(altitude_m) if altitude_m is not None else None,
                    float(speed_kmh) if speed_kmh is not None else None,
                    float(lat),
                    float(lon),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._conn.commit()
            self._sample_count += 1
            return True
        except Exception as exc:
            logger.error(f"LearningEngine DB write error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Calibrated distance estimation
    # ------------------------------------------------------------------

    def calibrated_distance_km(self, rssi_dbm: float) -> float:
        """Return a distance estimate using the locally calibrated model."""
        raw = rssi_to_distance_km(rssi_dbm)
        if self._sample_count < _MIN_SAMPLES_FOR_MODEL:
            return raw
        # Apply calibration offset: positive offset → reduce estimated distance
        adjusted_rssi = rssi_dbm - self._calibration_offset_db
        return rssi_to_distance_km(adjusted_rssi)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return current training statistics."""
        return {
            "sample_count": self._sample_count,
            "calibration_offset_db": round(self._calibration_offset_db, 3),
            "model_active": self._sample_count >= _MIN_SAMPLES_FOR_MODEL,
            "db_path": str(_DB_PATH),
        }

    def recent_samples(self, limit: int = 50) -> list:
        """Return the most recent *limit* training samples."""
        if not self._conn:
            return []
        try:
            rows = self._conn.execute(
                "SELECT * FROM training_samples ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error(f"LearningEngine query error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _recalibration_loop(self) -> None:
        """Periodically recalculate the calibration offset."""
        while self._running:
            await asyncio.sleep(_RECALIBRATION_INTERVAL_SECONDS)
            try:
                if self._sample_count >= _MIN_SAMPLES_FOR_MODEL:
                    self._recalculate_offset()
            except Exception as exc:
                logger.error(f"Recalibration error: {exc}")

    def _recalculate_offset(self) -> None:
        """Fit a scalar offset so Friis predictions match observed distances."""
        if not self._conn:
            return
        try:
            rows = self._conn.execute(
                "SELECT rssi_dbm, distance_km FROM training_samples ORDER BY id DESC LIMIT ?",
                (_MAX_CALIBRATION_SAMPLES,),
            ).fetchall()
            if not rows:
                return

            # Mean difference: log(observed / predicted) in dB-equivalent
            # predicted_km = rssi_to_distance_km(rssi_dbm)
            # We want: rssi_to_distance_km(rssi - offset) ≈ observed
            # Δ = mean(20 * log10(predicted / observed))
            deltas = []
            for r in rows:
                predicted = rssi_to_distance_km(r["rssi_dbm"])
                observed = r["distance_km"]
                if observed > 0 and predicted > 0:
                    deltas.append(20 * math.log10(predicted / observed))

            if deltas:
                self._calibration_offset_db = sum(deltas) / len(deltas)
                logger.info(
                    f"📐 Calibration updated: offset={self._calibration_offset_db:+.2f} dB "
                    f"from {len(deltas)} samples"
                )
        except Exception as exc:
            logger.error(f"_recalculate_offset error: {exc}")

    def _count_samples(self) -> int:
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM training_samples"
            ).fetchone()
            return row["n"] if row else 0
        except Exception:
            return 0


# Module-level singleton
_engine_instance: Optional[LearningEngine] = None


def get_learning_engine() -> LearningEngine:
    """Return the shared LearningEngine instance (creates on first call)."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = LearningEngine()
    return _engine_instance
