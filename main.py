#!/usr/bin/env python3
"""OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
from core.llm import LLMAnalyzer

sdr_manager = None
classifier = None
detector = None
llm_analyzer = None

# ---- Active WebSocket connections for live push ----
_ws_clients: list[WebSocket] = []

# ---- Request/response models ----
class AnalyzeRequest(BaseModel):
    hex_code: str
    callsign: Optional[str] = None
    altitude: Optional[float] = None
    ground_speed: Optional[float] = None
    track: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rssi: Optional[float] = None
    anomaly_type: Optional[str] = "GENERAL"

@app.on_event("startup")
async def startup():
    global sdr_manager, classifier, detector, llm_analyzer
    logger.info("="*80)
    logger.info("🚀 OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR | STARTING...")
    try:
        sdr_manager = SDRReader()
        logger.info("✅ Connected to dump1090 - REAL DATA MODE (PORT 30001)")
        classifier = get_classifier()
        logger.info("✅ AI Signal Classifier LOADED")
        detector = get_detector()
        logger.info("✅ Threat Detector INITIALIZED")
        llm_analyzer = LLMAnalyzer()
        logger.info("✅ LLM Analyzer READY")
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

# ======================================================================
# API v1 — Archive
# ======================================================================

@app.get("/api/v1/archive/aircraft")
async def api_archive_aircraft():
    """
    Return the full aircraft archive as a list of dicts suited for the
    web table in web/archive.html and web/index.html.

    Each entry: {hex, callsign, type, mil, pos}

    Data is loaded from the SQLite aircraft_archive table (populated by
    db/import_archive.py) with a fallback to data/aircraft_archive.json.
    """
    try:
        import sqlite3
        db_path = Path(__file__).parent / "db" / "ophir.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT hex, callsign, type, military AS mil, "
                "position_available AS pos FROM aircraft_archive"
            )
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            if rows:
                # Normalize boolean fields (SQLite stores 0/1)
                for row in rows:
                    row["mil"] = bool(row["mil"])
                    row["pos"] = bool(row["pos"])
                return rows
    except Exception as e:
        logger.warning(f"DB archive read failed, falling back to JSON: {e}")

    # JSON fallback
    json_path = Path(__file__).parent / "data" / "aircraft_archive.json"
    if json_path.exists():
        with open(json_path) as f:
            data = json.load(f)
        records = data.get("records", data) if isinstance(data, dict) else data
        result = []
        for r in records:
            result.append({
                "hex": r.get("hex", r.get("hex_code", "")),
                "callsign": r.get("callsign", ""),
                "type": r.get("type", r.get("aircraft_type", "UNKN")),
                "mil": bool(r.get("military", r.get("mil", False))),
                "pos": bool(r.get("position_available", r.get("pos", False))),
            })
        return result

    return []


# ======================================================================
# API v1 — LLM Analysis
# ======================================================================

@app.post("/api/v1/analyze")
async def api_analyze(req: AnalyzeRequest):
    """
    Analyse an aircraft via LLM (Ollama) with optional signal classification.

    Body: AnalyzeRequest JSON.
    Returns: {hex_code, classification, llm_analysis, anomaly_type}
    """
    aircraft_data = req.model_dump()

    # Signal classification (synchronous — no I/O)
    classification = None
    if classifier:
        try:
            classification = classifier.classify(aircraft_data)
        except Exception as e:
            logger.warning(f"Classifier error for {req.hex_code}: {e}")

    # LLM analysis (async I/O — may time out if Ollama is unavailable)
    llm_result = "LLM not available"
    if llm_analyzer:
        try:
            await llm_analyzer.init()
            llm_result = await llm_analyzer.analyze_anomaly(
                req.hex_code,
                req.anomaly_type or "GENERAL",
                aircraft_data,
            )
        except Exception as e:
            logger.warning(f"LLM analysis error for {req.hex_code}: {e}")
            llm_result = "LLM analysis failed"

    return {
        "hex_code": req.hex_code,
        "classification": classification,
        "llm_analysis": llm_result,
        "anomaly_type": req.anomaly_type,
        "analyzed_at": datetime.utcnow().isoformat(),
    }


# ======================================================================
# API v1 — Live aircraft
# ======================================================================

@app.get("/api/v1/live/aircraft")
async def api_live_aircraft():
    """
    Return all aircraft currently tracked by the SDR reader.
    Each entry is the raw dict from sdr_manager.aircraft_dict.
    """
    if not sdr_manager:
        return {"aircraft": [], "count": 0, "source": "none"}
    aircraft_list = list(sdr_manager.aircraft_dict.values())
    return {
        "aircraft": aircraft_list,
        "count": len(aircraft_list),
        "source": "dump1090",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ======================================================================
# WebSocket — live push
# ======================================================================

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """
    WebSocket endpoint for live aircraft updates.
    Sends the current aircraft list every second to connected clients.
    """
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info(f"WebSocket client connected. Total: {len(_ws_clients)}")
    try:
        import asyncio
        while True:
            aircraft_list = list(sdr_manager.aircraft_dict.values()) if sdr_manager else []
            await websocket.send_json({
                "type": "aircraft_update",
                "aircraft": aircraft_list,
                "count": len(aircraft_list),
                "timestamp": datetime.utcnow().isoformat(),
            })
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)

if __name__ == "__main__":
    logger.info("🚀 Starting OPHIR 2.0 Server")
    logger.info("📡 Listening on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, log_level="info")
