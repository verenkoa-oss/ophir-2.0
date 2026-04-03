"""
OPHIR LLM Module
Local LLM integration with Ollama for anomaly analysis
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
import time
from datetime import datetime, timezone
import httpx
import config

logger = logging.getLogger(__name__)

class LLMAnalyzer:
    """Analyze anomalies using local LLM"""
    
    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.OLLAMA_MODEL
        self.timeout = config.OLLAMA_TIMEOUT
        self.client = None
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
        """Initialize async HTTP client"""
        self.client = httpx.AsyncClient(timeout=self.timeout)
        self.ollama_connected = await self.check_connection()

    async def check_connection(self) -> bool:
        """Check if Ollama is running"""
        if not self.client:
            self.client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                logger.info(f"✅ Connected to Ollama at {self.base_url}")
                self.ollama_connected = True
                return True
            else:
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

    async def analyze_anomaly(self, hex_code: str, anomaly_type: str, 
                             aircraft_data: dict) -> str:
        """Analyze anomaly using LLM.

        Returns a placeholder immediately when LLM is disabled.
        """
        if not self.enabled:
            logger.debug(f"LLM disabled - skipping analysis for {hex_code}")
            return "LLM analysis is currently disabled."

        if not self.client:
            await self.init()

        prompt = self._build_prompt(hex_code, anomaly_type, aircraft_data)
        
        t_start = time.monotonic()
        try:
            response = await self.client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                analysis = result.get("response", "").strip()
                self.response_time_ms = round((time.monotonic() - t_start) * 1000, 1)
                self.last_analysis = _utcnow_iso()
                self.ollama_connected = True
                logger.info(f"LLM analysis for {hex_code}: {analysis[:100]}...")
                return analysis
            else:
                logger.error(f"LLM error: {response.status_code}")
                self.ollama_connected = False
                return "Analysis failed"
        
        except asyncio.TimeoutError:
            logger.error("LLM analysis timeout")
            self.ollama_connected = False
            return "Analysis timeout"
        except Exception as e:
            logger.error(f"LLM analysis error: {e}")
            self.ollama_connected = False
            return "Analysis error"
    
    def _build_prompt(self, hex_code: str, anomaly_type: str, aircraft_data: dict) -> str:
        """Build prompt for LLM"""
        return f"""
Analyze this ADS-B anomaly:

Aircraft ICAO: {hex_code}
Anomaly Type: {anomaly_type}
Callsign: {aircraft_data.get('callsign', 'Unknown')}
Altitude: {aircraft_data.get('altitude', 'N/A')} ft
Speed: {aircraft_data.get('ground_speed', 'N/A')} kt
RSSI: {aircraft_data.get('rssi', 'N/A')} dBm
Position: {aircraft_data.get('latitude', 'N/A')}, {aircraft_data.get('longitude', 'N/A')}

Provide brief security assessment (2-3 sentences).
"""
    
    async def close(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()
        logger.info("LLM connection closed")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def test_llm():
    """Test LLM connection"""
    llm = LLMAnalyzer()
    try:
        await llm.init()
        
        test_data = {
            'callsign': 'TEST123',
            'altitude': 35000,
            'ground_speed': 450,
            'rssi': -45,
            'latitude': 52.5,
            'longitude': 13.4
        }
        
        result = await llm.analyze_anomaly(
            'AABBCC',
            'UNUSUAL_SPEED_CHANGE',
            test_data
        )
        logger.info(f"Test result: {result}")
    
    except Exception as e:
        logger.error(f"LLM test failed: {e}")
    finally:
        await llm.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_llm())
