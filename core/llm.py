"""
OPHIR LLM Module
Local LLM integration with Ollama for anomaly analysis
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
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
    
    async def init(self):
        """Initialize async HTTP client"""
        self.client = httpx.AsyncClient(timeout=self.timeout)
        await self.check_connection()
    
    async def check_connection(self):
        """Check if Ollama is running"""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                logger.info(f"✅ Connected to Ollama at {self.base_url}")
                return True
            else:
                logger.error(f"❌ Ollama returned status {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Cannot connect to Ollama: {e}")
            return False
    
    async def analyze_anomaly(self, hex_code: str, anomaly_type: str, 
                             aircraft_data: dict) -> str:
        """Analyze anomaly using LLM"""
        if not self.client:
            await self.init()
        
        prompt = self._build_prompt(hex_code, anomaly_type, aircraft_data)
        
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
                logger.info(f"LLM analysis for {hex_code}: {analysis[:100]}...")
                return analysis
            else:
                logger.error(f"LLM error: {response.status_code}")
                return "Analysis failed"
        
        except asyncio.TimeoutError:
            logger.error("LLM analysis timeout")
            return "Analysis timeout"
        except Exception as e:
            logger.error(f"LLM analysis error: {e}")
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
