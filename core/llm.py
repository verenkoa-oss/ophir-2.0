"""
OPHIR LLM Module
Local LLM integration with Ollama for anomaly analysis
"""

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
        """Check that Ollama is reachable"""
        response = await self.client.get(f"{self.base_url}/api/tags")
        response.raise_for_status()
        logger.info("✅ Connected to Ollama at %s", self.base_url)

    async def analyze_anomaly(
        self,
        hex_code: str,
        anomaly_type: str,
        aircraft_data: dict | None = None,
    ) -> str:
        """Send anomaly data to the LLM and return the analysis text."""
        if self.client is None:
            raise RuntimeError("LLMAnalyzer not initialized — call init() first")

        aircraft_info = ""
        if aircraft_data:
            parts = []
            for key, value in aircraft_data.items():
                parts.append(f"{key}: {value}")
            aircraft_info = "\n".join(parts)

        prompt = (
            f"You are an aviation security analyst for the OPHIR system.\n"
            f"Analyse the following aircraft anomaly and provide a concise assessment.\n\n"
            f"Aircraft hex code: {hex_code}\n"
            f"Anomaly type: {anomaly_type}\n"
        )
        if aircraft_info:
            prompt += f"Additional data:\n{aircraft_info}\n"
        prompt += "\nProvide a brief threat assessment and recommended action."

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        response = await self.client.post(
            f"{self.base_url}/api/generate",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")

    async def close(self):
        """Close the HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
