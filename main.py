"""
OPHIR 2.0 — Main FastAPI application
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import config
from core.llm import LLMAnalyzer
from core.sdr import SDRReader

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory aircraft store
# ---------------------------------------------------------------------------

aircraft_store: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Global services
# ---------------------------------------------------------------------------

sdr: SDRReader | None = None
llm: LLMAnalyzer | None = None


# ---------------------------------------------------------------------------
# Lifespan context manager (replaces deprecated @app.on_event handlers)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic for OPHIR."""
    global sdr, llm

    logger.info("🚀 OPHIR 2.0 STARTUP")
    logger.info("Config: %s:%s", config.HOST, config.PORT)

    # Initialise LLM
    if config.ENABLE_LLM_ANALYSIS:
        try:
            llm = LLMAnalyzer()
            await llm.init()
        except Exception as exc:
            logger.warning("LLM init failed (continuing without LLM): %s", exc)
            llm = None

    # Initialise SDR reader
    def _on_aircraft(data: dict) -> None:
        hex_code = data.get("hex", "").upper()
        if hex_code:
            aircraft_store[hex_code] = data

    try:
        sdr = SDRReader(on_aircraft=_on_aircraft)
        await sdr.start()
        logger.info("✅ SDR reader started")
    except Exception as exc:
        logger.error("SDR reader task error: %s", exc)
        sdr = None

    logger.info("✅ OPHIR startup complete")

    yield  # Application runs here

    # Shutdown
    logger.info("🛑 OPHIR shutdown")
    if sdr:
        await sdr.stop()
    if llm:
        await llm.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OPHIR 2.0",
    description="Aircraft anomaly detection and LLM analysis",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    hex: str
    callsign: str | None = None
    altitude: int | None = None
    speed: float | None = None
    anomalies: list[str] = []


class AnalyzeResponse(BaseModel):
    hex: str
    analysis: str
    model: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {
        "service": "OPHIR 2.0",
        "status": "running",
        "routes": [
            "/health",
            "/aircraft",
            "/aircraft/{hex_code}",
            "/stats",
            "/api/v1/analyze",
            "/api/v1/archive/aircraft",
        ],
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "llm_available": llm is not None,
        "sdr_available": sdr is not None,
    }


@app.get("/aircraft")
async def get_aircraft():
    return {"aircraft": list(aircraft_store.values()), "count": len(aircraft_store)}


@app.get("/aircraft/{hex_code}")
async def get_aircraft_by_hex(hex_code: str):
    key = hex_code.upper()
    if key not in aircraft_store:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    aircraft = aircraft_store[key]

    # Optionally enrich with LLM analysis for any flagged anomalies
    analysis: str | None = None
    if config.ENABLE_LLM_ANALYSIS and llm:
        try:
            analysis = await llm.analyze_anomaly(
                hex_code=key,
                anomaly_type="on_demand_lookup",
                aircraft_data=aircraft,
            )
        except Exception as exc:
            logger.warning("LLM analysis failed for %s: %s", key, exc)

    return {"aircraft": aircraft, "analysis": analysis}


@app.get("/stats")
async def stats():
    return {
        "aircraft_tracked": len(aircraft_store),
        "llm_enabled": config.ENABLE_LLM_ANALYSIS,
        "llm_model": config.OLLAMA_MODEL,
        "sdr_host": config.DUMP1090_HOST,
        "sdr_port": config.DUMP1090_PORT,
    }


@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """Accept aircraft anomaly data and return LLM analysis."""
    if not config.ENABLE_LLM_ANALYSIS:
        raise HTTPException(status_code=503, detail="LLM analysis is disabled")
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM service is unavailable")

    aircraft_data: dict[str, Any] = {}
    if request.callsign is not None:
        aircraft_data["callsign"] = request.callsign
    if request.altitude is not None:
        aircraft_data["altitude"] = request.altitude
    if request.speed is not None:
        aircraft_data["speed"] = request.speed

    anomaly_type = ", ".join(request.anomalies) if request.anomalies else "unknown"

    try:
        analysis = await llm.analyze_anomaly(
            hex_code=request.hex,
            anomaly_type=anomaly_type,
            aircraft_data=aircraft_data,
        )
    except Exception as exc:
        logger.error("LLM analysis error for %s: %s", request.hex, exc)
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    return AnalyzeResponse(hex=request.hex, analysis=analysis, model=config.OLLAMA_MODEL)


@app.get("/api/v1/archive/aircraft")
async def archive_aircraft():
    """Return aircraft records from the JSON archive."""
    archive_path = config.AIRCRAFT_ARCHIVE_PATH
    if not os.path.exists(archive_path):
        return {"aircraft": [], "count": 0}
    try:
        with open(archive_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {"aircraft": data, "count": len(data)}
        return {"aircraft": data, "count": 1}
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Archive read error: {exc}") from exc


@app.get("/web/{file_path:path}")
async def serve_web(file_path: str):
    """Serve static files from the web directory."""
    web_root = Path("web").resolve()
    requested = (web_root / file_path).resolve()
    if not requested.is_relative_to(web_root):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not requested.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(requested))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
