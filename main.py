#!/usr/bin/env python3
"""OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('/tmp/ophir.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="OPHIR 2.0 | AEGIS-X", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

from core.sdr_real import SDRReader
from core.signal_classifier import get_classifier
from core.threat_detector import get_detector

sdr_manager = None
classifier = None
detector = None

@app.on_event("startup")
async def startup():
    global sdr_manager, classifier, detector
    logger.info("="*80)
    logger.info("🚀 OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR | STARTING...")
    try:
        sdr_manager = SDRReader()
        logger.info("✅ Connected to dump1090 - REAL DATA MODE (PORT 30001)")
        classifier = get_classifier()
        logger.info("✅ AI Signal Classifier LOADED")
        detector = get_detector()
        logger.info("✅ Threat Detector INITIALIZED")
        logger.info("="*80)
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")

@app.on_event("shutdown")
async def shutdown():
    logger.info("🛑 OPHIR 2.0 shutting down...")

@app.get("/")
async def root():
    return {"name": "OPHIR 2.0 | AEGIS-X", "version": "2.0.0", "status": "operational"}

@app.get("/health")
async def health():
    return {"status": "healthy", "sdr_connected": sdr_manager is not None, "detector_loaded": detector is not None}

@app.get("/aircraft")
async def get_aircraft():
    if not sdr_manager:
        return {"aircraft": [], "count": 0}
    aircraft_list = list(sdr_manager.aircraft_dict.values())
    return {"aircraft": aircraft_list, "count": len(aircraft_list)}

@app.get("/noise")
async def get_noise():
    if not sdr_manager:
        return {"current_signal_type": "UNKNOWN", "signal_confidence": 0}
    try:
        noise_data = await sdr_manager.get_noise_data()
        return {
            "current_signal_type": noise_data.get('signal_type', 'UNKNOWN'),
            "signal_confidence": noise_data.get('confidence', 0),
            "noise_dbm": noise_data.get('noise_dbm', 0)
        }
    except Exception as e:
        logger.error(f"Noise error: {e}")
        return {"current_signal_type": "ERROR", "error": str(e)}

@app.get("/events")
async def get_events():
    if not sdr_manager:
        return {"events": [], "count": 0}
    try:
        events = await sdr_manager.get_signal_events()
        return {"events": events[-50:] if events else [], "count": len(events)}
    except Exception as e:
        logger.error(f"Events error: {e}")
        return {"events": [], "count": 0}

@app.get("/threat/current")
async def get_current_threat():
    """Get current threat assessment"""
    if not sdr_manager or not detector:
        return {"threat": "NO DATA", "status": "waiting"}
    
    try:
        noise_data = await sdr_manager.get_noise_data()
        anomaly = detector.detect_anomaly() if hasattr(detector, "detect_anomaly") else None
        return {"threat": "NORMAL" if not anomaly else "THREAT", "anomaly_detected": anomaly is not None}
    except Exception as e:
        logger.error(f"Threat error: {e}")
        return {"threat": "ERROR", "error": str(e)}

@app.get("/threat/history")
async def get_threat_history():
    """Get threat history (last 50)"""
    if not detector:
        return {"threats": [], "count": 0}
    try:
        threats = []
        if hasattr(detector, 'noise_history'):
            for signal in detector.noise_history[-50:]:
                threats.append(signal)
        return {"threats": threats, "count": len(threats)}
    except Exception as e:
        logger.error(f"History error: {e}")
        return {"threats": [], "count": 0}

@app.get("/threat/anomalies")
async def get_anomalies():
    """Get detected anomalies"""
    if not detector:
        return {"status": "NO DATA"}
    try:
        if hasattr(detector, 'detect_anomaly'):
            anomaly = detector.detect_anomaly()
            if anomaly:
                return {"status": "🚨 ANOMALY DETECTED!", "details": anomaly}
        return {"status": "🟢 Normal", "details": None}
    except Exception as e:
        logger.error(f"Anomalies error: {e}")
        return {"status": "ERROR"}

@app.get("/dashboard.html")
async def get_dashboard():
    if os.path.exists("dashboard.html"):
        return FileResponse("dashboard.html", media_type="text/html")
    elif os.path.exists("web/dashboard.html"):
        return FileResponse("web/dashboard.html", media_type="text/html")
    else:
        raise HTTPException(status_code=404, detail="Dashboard not found")


# ---------------------------------------------------------------------------
# v1 API endpoints
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    hex_code: str
    anomaly_type: Optional[str] = "UNKNOWN"
    aircraft_data: Optional[dict] = {}


@app.post("/api/v1/analyze")
async def analyze_aircraft(req: AnalyzeRequest):
    """Analyse aircraft data with the local LLM."""
    analysis_text = "Analysis unavailable"
    try:
        from core.llm import LLMAnalyzer
        llm = LLMAnalyzer()
        await llm.init()
        raw = await llm.analyze_anomaly(
            req.hex_code,
            req.anomaly_type,
            req.aircraft_data or {},
        )
        await llm.close()
        # Only surface the result when it is clearly a successful LLM response
        # (not an internal error message that may embed exception details).
        if raw and not raw.startswith(("Error:", "Analysis failed", "Analysis timeout")):
            analysis_text = raw
    except Exception as exc:
        logger.error("/api/v1/analyze error: %s", type(exc).__name__)
    return {"hex_code": req.hex_code, "analysis": analysis_text, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/archive/aircraft")
async def archive_aircraft():
    """Return all aircraft stored in the database archive."""
    try:
        from core.database import db_manager
        session = db_manager.get_sync_session()
        try:
            aircraft_list = db_manager.get_all_aircraft(session)
            return {
                "aircraft": [
                    {
                        "hex_code": ac.hex_code,
                        "callsign": ac.callsign,
                        "aircraft_type": ac.aircraft_type,
                        "country": ac.country,
                        "latitude": ac.latitude,
                        "longitude": ac.longitude,
                        "altitude": ac.altitude,
                        "ground_speed": ac.ground_speed,
                        "track": ac.track,
                        "rssi": ac.rssi,
                        "is_shadow": ac.is_shadow,
                        "first_seen": ac.first_seen.isoformat() if ac.first_seen else None,
                        "last_seen": ac.last_seen.isoformat() if ac.last_seen else None,
                    }
                    for ac in aircraft_list
                ],
                "count": len(aircraft_list),
            }
        finally:
            session.close()
    except Exception as exc:
        logger.error(f"/api/v1/archive/aircraft error: {exc}")
        raise HTTPException(status_code=500, detail="Failed to retrieve archive")


@app.get("/api/v1/live/aircraft")
async def live_aircraft():
    """Return currently tracked live aircraft."""
    if not sdr_manager:
        return {"aircraft": [], "count": 0, "status": "SDR not connected"}
    aircraft_list = list(sdr_manager.aircraft_dict.values())
    return {
        "aircraft": aircraft_list,
        "count": len(aircraft_list),
        "status": "live",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# Active WebSocket connections
_ws_clients: list[WebSocket] = []


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """WebSocket endpoint for real-time aircraft updates."""
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info(f"WebSocket client connected ({len(_ws_clients)} total)")
    try:
        while True:
            # Push current aircraft state every second
            if sdr_manager:
                payload = {
                    "type": "aircraft_update",
                    "aircraft": list(sdr_manager.aircraft_dict.values()),
                    "count": len(sdr_manager.aircraft_dict),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            else:
                payload = {"type": "waiting", "message": "SDR not connected"}
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        _ws_clients.remove(websocket)
        logger.info(f"WebSocket client disconnected ({len(_ws_clients)} total)")
    except Exception as exc:
        logger.error(f"WebSocket error: {exc}")
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


    logger.info("🚀 Starting OPHIR 2.0 Server")
    logger.info("📡 Listening on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, log_level="info")
