"""
OPHIR 2.0 | AEGIS-X Configuration Module
"""

import os
from pathlib import Path
from enum import Enum

PROJECT_ROOT = Path(__file__).parent
VENV_ROOT = PROJECT_ROOT / "venv"

LOGS_DIR = PROJECT_ROOT / "logs"
DB_DIR = PROJECT_ROOT / "data" / "ophir_db"
CACHE_DIR = PROJECT_ROOT / "data" / "ophir_cache"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "ophir_archive"

for path in [LOGS_DIR, DB_DIR, CACHE_DIR, ARCHIVE_DIR]:
    path.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_DIR / 'ophir.db'}"
DB_ECHO = False
DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 20

API_HOST = "0.0.0.0"
API_PORT = 8080
API_WORKERS = 8
API_RELOAD = False
API_LOG_LEVEL = "info"

DUMP1090_JSON_PATH = Path("/run/dump1090-mutability/data/aircraft.json")
DUMP1090_HOST = "localhost"
DUMP1090_PORT = 30005
SDR_UPDATE_INTERVAL = 1.0

SDR_GAIN = 45
SDR_PPM = 0
SDR_FREQ = 1090_000_000

AIRCRAFT_DB_FILE = PROJECT_ROOT / "data" / "ophir_aircrafts.json"
AIRCRAFT_DB_UPDATE_INTERVAL = 3600

SHADOW_RSSI_THRESHOLD = -90
SHADOW_DISTANCE_MODEL = "friis"
SHADOW_MAX_HISTORY = 10000

# Antenna profiles
class AntennaMode(str, Enum):
    GARAGE = "GARAGE"
    AIR = "AIR"

ANTENNA_PROFILES: dict = {
    AntennaMode.GARAGE: {
        "rssi_threshold": -75,
        "gain": 35,
        "description": "Indoor/garage antenna – reduced range, higher noise floor",
    },
    AntennaMode.AIR: {
        "rssi_threshold": -90,
        "gain": 45,
        "description": "Outdoor/airborne antenna – maximum range, low noise floor",
    },
}

DEFAULT_ANTENNA_MODE: AntennaMode = AntennaMode.AIR

OBSERVER_LATITUDE = 31.073541   # N 31° 4' 24.749''
OBSERVER_LONGITUDE = 35.037383  # E 35° 2' 14.577''
OBSERVER_LOCATION = "Middle East - Military Zone"

# Gain and noise defaults
SDR_GAIN_MIN = -5
SDR_GAIN_MAX = 45
SDR_GAIN_DEFAULT = 45

NOISE_THRESHOLD_DEFAULT = -75
NOISE_THRESHOLD_MIN = -100
NOISE_THRESHOLD_MAX = -30

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "mistral:latest"
OLLAMA_TIMEOUT = 60.0
LLM_REPORT_SCHEDULE = "00:00"

# LLM runtime defaults
LLM_ENABLED_DEFAULT = True

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
LOG_FILE_MAX_SIZE = 100 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5
LOG_RETENTION_DAYS = 30

ASYNC_WORKERS = 8
DB_COMMIT_BATCH_SIZE = 100
CACHE_TTL = 300

SECRET_KEY = os.getenv("OPHIR_SECRET_KEY", "dev-secret-change-in-production")
ALLOWED_ORIGINS = ["http://localhost", "http://localhost:8080", "http://127.0.0.1:8080"]

ENABLE_SHADOW_TRACKING = True
ENABLE_LLM_ANALYSIS = True
ENABLE_WEBSOCKET = True
ENABLE_HISTORY_EXPORT = True

class AircraftType(str, Enum):
    MILITARY = "military"
    CIVILIAN = "civilian"
    UNKNOWN = "unknown"

class TargetClass(str, Enum):
    CONFIRMED = "confirmed"
    SHADOW = "shadow"
    ANOMALY = "anomaly"
