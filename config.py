"""
OPHIR 2.0 Configuration
"""

import os
from enum import Enum
from pathlib import Path

# Server settings
HOST = "0.0.0.0"
PORT = 8080

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

# Observer Location
OBSERVER_LATITUDE = 31.073541
OBSERVER_LONGITUDE = 35.037383
# Aliases used throughout the codebase
OBSERVER_LAT = OBSERVER_LATITUDE
OBSERVER_LON = OBSERVER_LONGITUDE
OBSERVER_LOCATION = "Middle East - Signal Intelligence Zone"

API_HOST = "0.0.0.0"
API_PORT = 8080
API_WORKERS = 8
API_RELOAD = os.getenv("OPHIR_API_RELOAD", "false").lower() == "true"
API_LOG_LEVEL = "info"

DUMP1090_JSON_PATH = Path("/run/dump1090-mutability/data/aircraft.json")
DUMP1090_HOST = "localhost"
DUMP1090_PORT = 30001
DUMP1090_MODE = "basic"
SDR_UPDATE_INTERVAL = 1.0

# WebSocket
WS_HEARTBEAT = 1.0  # seconds
WS_BUFFER_SIZE = 100

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

# LLM settings
LLM_ENABLED_DEFAULT = True
ENABLE_LLM_ANALYSIS = True
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "mistral:latest"
OLLAMA_TIMEOUT = 60.0

# Distance calculator constants (Friis free-space path loss model for 1090 MHz ADS-B)
DISTANCE_FREQ_HZ = 1_090_000_000.0   # 1090 MHz
DISTANCE_TX_POWER_DBM = 54.0         # Typical ADS-B EIRP ~250 W
DISTANCE_TX_GAIN_DBI = 0.0
DISTANCE_RX_GAIN_DBI = 0.0
DISTANCE_MIN_KM = 0.1
DISTANCE_MAX_KM = 600.0

# Archive
AIRCRAFT_ARCHIVE_PATH = "data/aircraft_archive.json"
