"""
OPHIR LLM Module
Local LLM integration with Ollama for anomaly analysis.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

import config

logger = logging.getLogger(__name__)


class LLMAnalyzer:
    """Analyze anomalies using a local Ollama LLM."""

    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = config.OLLAMA_TIMEOUT
        self.client: httpx.AsyncClient | None = None
        # Runtime state
        self.enabled: bool = config.LLM_ENABLED_DEFAULT
        self.ollama_connected: bool = False
        self.response_time_ms: float | None = None
        self.last_analysis: str | None = None

    # ------------------------------------------------------------------
    # Enable / disable at runtime
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        logger.info(
            f"{'✅' if enabled else '🔕'} LLM analysis {'enabled' if enabled else 'disabled'}"
        )

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    async def init(self):
        """Initialize async HTTP client."""
        self.client = httpx.AsyncClient(timeout=self.timeout)
        self.ollama_connected = await self.check_connection()

    async def check_connection(self) -> bool:
        """Check if Ollama is running. Returns True when reachable."""
        if not self.client:
            self.client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                logger.info(f"✅ Connected to Ollama at {self.base_url}")
                self.ollama_connected = True
                return True
            logger.error(f"❌ Ollama returned status {response.status_code}")
            self.ollama_connected = False
            return False
        except Exception as e:
            logger.error(f"❌ Cannot connect to Ollama: {e}")
            self.ollama_connected = False
            return False

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    async def analyze_anomaly(
        self,
        hex_code: str,
        anomaly_type: str,
        aircraft_data: dict | None = None,
    ) -> str:
        """Analyze an anomaly using the LLM.

        Returns a placeholder immediately when LLM is disabled or unavailable.
        """
        if not self.enabled:
            logger.debug(f"LLM disabled - skipping analysis for {hex_code}")
            return "LLM analysis is currently disabled."

        if not self.client:
            await self.init()

        prompt = self._build_prompt(hex_code, anomaly_type, aircraft_data or {})

        t_start = time.monotonic()
        try:
            response = await self.client.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
            )

            if response.status_code == 200:
                result = response.json()
                analysis = result.get("response", "").strip()
                self.response_time_ms = round((time.monotonic() - t_start) * 1000, 1)
                self.last_analysis = _utcnow_iso()
                self.ollama_connected = True
                logger.info(f"LLM analysis for {hex_code}: {analysis[:100]}...")
                return analysis

            logger.error(f"LLM error: {response.status_code}")
            self.ollama_connected = False
            return "Analysis failed"

        except asyncio.TimeoutError:
            logger.error("LLM analysis timeout")
            self.ollama_connected = False
            return "Analysis timeout"
        except Exception as e:
            logger.error(f"LLM analysis error: {e}")
            return "Analysis error"

    def _build_prompt(self, hex_code: str, anomaly_type: str, aircraft_data: dict) -> str:
        """Build the analysis prompt."""
        return (
            f"Analyze this ADS-B anomaly:\n\n"
            f"Aircraft ICAO: {hex_code}\n"
            f"Anomaly Type: {anomaly_type}\n"
            f"Callsign: {aircraft_data.get('callsign', 'Unknown')}\n"
            f"Altitude: {aircraft_data.get('altitude', 'N/A')} ft\n"
            f"Speed: {aircraft_data.get('ground_speed', 'N/A')} kt\n"
            f"RSSI: {aircraft_data.get('rssi', 'N/A')} dBm\n"
            f"Position: {aircraft_data.get('latitude', 'N/A')}, "
            f"{aircraft_data.get('longitude', 'N/A')}\n\n"
            f"Provide brief security assessment (2-3 sentences)."
        )

    async def close(self):
        """Close the HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None
        logger.info("LLM connection closed")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
