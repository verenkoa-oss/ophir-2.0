#!/usr/bin/env python3
"""OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import json
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
from core.database import db_manager
from core.llm import LLMAnalyzer
from db.schema import Aircraft

sdr_manager = None
classifier = None
detector = None
llm_analyzer = None

# Active WebSocket connections
_ws_aircraft_clients: list[WebSocket] = []
_ws_threats_clients: list[WebSocket] = []

# Background task handles for proper lifecycle management
_tracking_task = None
_broadcast_task = None

@app.on_event("startup")
async def startup():
    global sdr_manager, classifier, detector, llm_analyzer, _tracking_task, _broadcast_task
    logger.info("="*80)
    logger.info("🚀 OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR | STARTING...")
    try:
        sdr_manager = SDRReader()
        _tracking_task = asyncio.create_task(sdr_manager.start_tracking())
        logger.info("✅ Connected to dump1090 - REAL DATA MODE (PORT 30001)")
        classifier = get_classifier()
        logger.info("✅ AI Signal Classifier LOADED")
        detector = get_detector()
        logger.info("✅ Threat Detector INITIALIZED")
        llm_analyzer = LLMAnalyzer()
        logger.info("✅ LLM Analyzer INITIALIZED")
        # Background task: broadcast live aircraft to WebSocket clients
        _broadcast_task = asyncio.create_task(_broadcast_loop())
        logger.info("="*80)
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")

@app.on_event("shutdown")
async def shutdown():
    global sdr_manager, llm_analyzer, _tracking_task, _broadcast_task
    logger.info("🛑 OPHIR 2.0 shutting down...")
    if _broadcast_task:
        _broadcast_task.cancel()
        try:
            await _broadcast_task
        except asyncio.CancelledError:
            pass
    if sdr_manager:
        await sdr_manager.stop_tracking()
    if llm_analyzer:
        await llm_analyzer.close()


async def _broadcast_loop():
    """Periodically push live aircraft data to all connected WebSocket clients."""
    while True:
        await asyncio.sleep(2)
        if not sdr_manager:
            continue
        aircraft_list = list(sdr_manager.aircraft_dict.values())
        payload = json.dumps({"aircraft": aircraft_list, "count": len(aircraft_list)})
        dead_aircraft_clients: list[WebSocket] = []
        for ws in _ws_aircraft_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead_aircraft_clients.append(ws)
        for ws in dead_aircraft_clients:
            if ws in _ws_aircraft_clients:
                _ws_aircraft_clients.remove(ws)

        # Threat broadcast
        if detector:
            for ac in aircraft_list:
                anomaly = detector.detect_anomaly(ac)
                if anomaly:
                    threat_payload = json.dumps(anomaly)
                    dead_threat_clients: list[WebSocket] = []
                    for ws in _ws_threats_clients:
                        try:
                            await ws.send_text(threat_payload)
                        except Exception:
                            dead_threat_clients.append(ws)
                    for ws in dead_threat_clients:
                        if ws in _ws_threats_clients:
                            _ws_threats_clients.remove(ws)

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
# /api/v1 endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/analyze")
async def analyze_aircraft(aircraft_data: dict):
    """Accept aircraft data and return LLM analysis."""
    if not aircraft_data:
        raise HTTPException(status_code=400, detail="aircraft_data is required")

    hex_code = aircraft_data.get("hex_code", "UNKNOWN")
    anomaly_type = aircraft_data.get("anomaly_type", "GENERAL_ANALYSIS")

    if llm_analyzer:
        try:
            analysis = await llm_analyzer.analyze_anomaly(hex_code, anomaly_type, aircraft_data)
        except Exception as e:
            logger.error(f"LLM analysis error: {e}")
            analysis = "LLM analysis unavailable"
    else:
        analysis = "LLM analyzer not initialized"

    if classifier:
        classification = classifier.classify(aircraft_data)
    else:
        classification = {"category": "UNKNOWN", "confidence": 0.0, "reason": "classifier not ready"}

    return {
        "hex_code": hex_code,
        "analysis": analysis,
        "classification": classification,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/v1/archive/aircraft")
async def get_archive_aircraft():
    """Return all aircraft records stored in the database."""
    try:
        session = db_manager.get_sync_session()
        try:
            records = session.query(Aircraft).all()
            result = []
            for ac in records:
                result.append({
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
                })
        finally:
            session.close()
        return {"aircraft": result, "count": len(result)}
    except Exception as e:
        logger.error(f"Archive error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve aircraft archive")


@app.websocket("/api/v1/live/aircraft")
async def ws_live_aircraft(websocket: WebSocket):
    """WebSocket endpoint – streams live aircraft data every 2 seconds."""
    await websocket.accept()
    _ws_aircraft_clients.append(websocket)
    logger.info(f"WS /api/v1/live/aircraft client connected ({len(_ws_aircraft_clients)} total)")
    try:
        while True:
            # Keep the connection alive; data is pushed by _broadcast_loop
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_aircraft_clients:
            _ws_aircraft_clients.remove(websocket)
        logger.info("WS /api/v1/live/aircraft client disconnected")


@app.websocket("/api/v1/threats/live")
async def ws_live_threats(websocket: WebSocket):
    """WebSocket endpoint – streams threat/anomaly events."""
    await websocket.accept()
    _ws_threats_clients.append(websocket)
    logger.info(f"WS /api/v1/threats/live client connected ({len(_ws_threats_clients)} total)")
    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_threats_clients:
            _ws_threats_clients.remove(websocket)
        logger.info("WS /api/v1/threats/live client disconnected")

if __name__ == "__main__":
    logger.info("🚀 Starting OPHIR 2.0 Server")
    logger.info("📡 Listening on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, log_level="info")
