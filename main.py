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
#!/usr/bin/env python3
"""OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import collections
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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
import config

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
_osc_task = None

# ---------------------------------------------------------------------------
# Runtime state – managed by the three control endpoints
# ---------------------------------------------------------------------------

# Antenna mode: initialised from config default
_antenna_mode: config.AntennaMode = config.DEFAULT_ANTENNA_MODE

# LLM enabled flag
_llm_enabled: bool = config.LLM_ENABLED_DEFAULT

# dump1090 uptime tracking (monotonic clock reference, set only when confirmed running)
_dump1090_start_monotonic: float | None = None

# Delay between stop and start during restart
_DUMP1090_RESTART_DELAY: float = 1.0

# Timeout for quick Ollama connectivity check in the status endpoint
_OLLAMA_STATUS_CHECK_TIMEOUT: float = 3.0

# ---------------------------------------------------------------------------
# Signal Intelligence Control state
# ---------------------------------------------------------------------------

# Oscilloscope ring buffers (one sample per second, last 60 seconds)
_OSC_SIZE = 60
_osc_ch1: collections.deque = collections.deque(maxlen=_OSC_SIZE)   # ADS-B RSSI
_osc_ch2: collections.deque = collections.deque(maxlen=_OSC_SIZE)   # RF noise floor
_osc_times: collections.deque = collections.deque(maxlen=_OSC_SIZE) # ISO timestamps
_ws_osc_clients: list[WebSocket] = []

# Gain control (-5 … +40 dB, stored at runtime)
_current_gain: float = float(config.SDR_GAIN)

# Noise threshold / filter settings
_noise_threshold_dbm: float = -75.0
_noise_filter_enabled: bool = True
_noise_alert_enabled: bool = True
_noise_show_events: bool = True
_noise_spike_events: list = []  # {dbm, ts} dicts for the last 5 min

# System runtime
_system_start_monotonic: float = time.monotonic()
_signals_processed: int = 0

@app.on_event("startup")
async def startup():
    global sdr_manager, classifier, detector, llm_analyzer, _tracking_task, _broadcast_task
    global _antenna_mode, _llm_enabled, _dump1090_start_monotonic
    global _system_start_monotonic, _osc_task
    logger.info("="*80)
    logger.info("🚀 OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR | STARTING...")
    try:
        sdr_manager = SDRReader()

        # Connect to dump1090 and start _continuous_read() background task
        connected = await sdr_manager.connect()
        if not connected:
            logger.error("❌ Failed to connect to dump1090 - check that dump1090 --net is running")
        else:
            logger.info("✅ Connected to dump1090 - REAL DATA MODE (PORT 30001)")

        # Apply the default antenna mode from config
        sdr_manager.set_antenna_mode(_antenna_mode)
        _tracking_task = asyncio.create_task(sdr_manager.start_tracking())
        # Only record uptime reference if dump1090 is actually reachable
        if _get_dump1090_pid():
            _dump1090_start_monotonic = time.monotonic()
        logger.info("✅ Connected to dump1090 - REAL DATA MODE (PORT 30001)")
        classifier = get_classifier()
        logger.info("✅ AI Signal Classifier LOADED")
        detector = get_detector()
        logger.info("✅ Threat Detector INITIALIZED")
        llm_analyzer = LLMAnalyzer()
        llm_analyzer.set_enabled(_llm_enabled)
        logger.info(f"✅ LLM Analyzer INITIALIZED (enabled={_llm_enabled})")
        # Background task: broadcast live aircraft to WebSocket clients
        _broadcast_task = asyncio.create_task(_broadcast_loop())
        # Background task: sample oscilloscope data and push to WS clients
        _osc_task = asyncio.create_task(_oscilloscope_sample_loop())
        _system_start_monotonic = time.monotonic()
        logger.info("="*80)
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")

@app.on_event("shutdown")
async def shutdown():
    global sdr_manager
    logger.info("🛑 OPHIR 2.0 shutting down...")
    if sdr_manager:
        await sdr_manager.close()

@app.get("/")
async def root():
    for path in ("web/dashboard.html", "dashboard.html"):
        if os.path.exists(path):
            return FileResponse(path, media_type="text/html")
    global sdr_manager, llm_analyzer, _tracking_task, _broadcast_task, _osc_task
    logger.info("🛑 OPHIR 2.0 shutting down...")
    for task in (_broadcast_task, _osc_task):
        if task:
            task.cancel()
            try:
                await task
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


def _compute_stats(values: list) -> dict:
    """Return max/min/avg for a list of numbers (None-safe)."""
    clean = [v for v in values if v is not None]
    if not clean:
        return {"max": None, "min": None, "avg": None}
    return {
        "max": round(max(clean), 1),
        "min": round(min(clean), 1),
        "avg": round(sum(clean) / len(clean), 1),
    }


async def _oscilloscope_sample_loop():
    """Sample RSSI / noise data every second and push to oscilloscope WS clients."""
    global _signals_processed, _noise_spike_events
    while True:
        await asyncio.sleep(1)
        if not sdr_manager:
            continue

        aircraft_list = list(sdr_manager.aircraft_dict.values())
        _signals_processed += len(aircraft_list)

        # CH1: strongest ADS-B RSSI in current snapshot
        rssi_values = [ac.get("rssi") for ac in aircraft_list if ac.get("rssi") is not None]
        ch1_val: float | None = max(rssi_values) if rssi_values else None

        # CH2: rolling average noise from recent noise history
        ch2_val: float | None = None
        if sdr_manager._noise_history:
            recent = sdr_manager._noise_history[-10:]
            ch2_val = round(sum(r["noise_dbm"] for r in recent) / len(recent), 1)

        now_iso = datetime.now(timezone.utc).isoformat()
        _osc_ch1.append(ch1_val)
        _osc_ch2.append(ch2_val)
        _osc_times.append(now_iso)

        # Noise spike detection
        if _noise_alert_enabled and ch2_val is not None and ch2_val > _noise_threshold_dbm:
            _noise_spike_events.append({"dbm": ch2_val, "ts": time.time()})

        # Trim spike events older than 5 minutes
        cutoff = time.time() - 300
        _noise_spike_events[:] = [e for e in _noise_spike_events if e["ts"] >= cutoff]

        # Push to oscilloscope WebSocket subscribers
        if not _ws_osc_clients:
            continue
        payload = json.dumps({
            "ch1": list(_osc_ch1),
            "ch2": list(_osc_ch2),
            "timestamps": list(_osc_times),
            "timestamp": now_iso,
            "ch1_stats": _compute_stats(list(_osc_ch1)),
            "ch2_stats": _compute_stats(list(_osc_ch2)),
        })
        dead: list[WebSocket] = []
        for ws in _ws_osc_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _ws_osc_clients:
                _ws_osc_clients.remove(ws)


@app.get("/")
async def root():
    """Serve the main dashboard."""
    for candidate in ("web/dashboard.html", "dashboard.html"):
        if os.path.exists(candidate):
            return FileResponse(candidate, media_type="text/html")
    return {"name": "OPHIR 2.0 | AEGIS-X", "version": "2.0.0", "status": "operational"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "sdr_connected": sdr_manager is not None and getattr(sdr_manager, "connected", False),
        "detector_loaded": detector is not None,
    }

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
            "noise_dbm": noise_data.get('noise_dbm', -95.0),
            "adsb_dbm": noise_data.get('adsb_dbm', -95.0),
            "noise_history": noise_data.get('noise_history', []),
            "adsb_history": noise_data.get('adsb_history', []),
        }
    except Exception as e:
        logger.error(f"Noise error: {e}")
        return {"current_signal_type": "ERROR", "error": str(e)}

@app.get("/raw")
async def get_raw():
    """Return recent raw dump1090 messages for terminal display"""
    if not sdr_manager:
        return {"messages": [], "connected": False}
    try:
        noise_data = await sdr_manager.get_noise_data()
        return {
            "messages": noise_data.get('raw_messages', []),
            "connected": getattr(sdr_manager, 'connected', False),
        }
    except Exception as e:
        logger.error(f"Raw error: {e}")
        return {"messages": [], "connected": False}

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
