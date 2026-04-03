"""
OPHIR 2.0 Configuration
"""

# Server settings
HOST = "0.0.0.0"
PORT = 8080

# SDR / dump1090 settings
DUMP1090_HOST = "localhost"
DUMP1090_PORT = 30003

# LLM settings
ENABLE_LLM_ANALYSIS = True
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "mistral:latest"
OLLAMA_TIMEOUT = 60.0

# Archive
AIRCRAFT_ARCHIVE_PATH = "data/aircraft_archive.json"
