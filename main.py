#!/usr/bin/env python3
"""OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
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

# Serve static web assets (CSS, JS, HTML) from /web directory
_web_dir = Path(__file__).parent / "web"
if _web_dir.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/web", StaticFiles(directory=str(_web_dir)), name="web")

from core.sdr_real import SDRReader
from core.signal_classifier import get_classifier
from core.threat_detector import get_detector
from core.database import db_manager
from core.llm import LLMAnalyzer
from db.schema import Aircraft
from distance_calculator import estimate_aircraft_distance
from learning_engine import get_learning_engine
import config

sdr_manager = None
classifier = None
detector = None
llm_analyzer = None
learning_engine = None

# Active WebSocket connections
_ws_aircraft_clients: list[WebSocket] = []
_ws_threats_clients: list[WebSocket] = []
_ws_oscilloscope_clients: list[WebSocket] = []

# Background task handles for proper lifecycle management
_tracking_task = None
_broadcast_task = None
_oscilloscope_task = None

# Runtime gain / noise state
_runtime_gain_db: float = float(config.SDR_GAIN)
_runtime_noise_threshold_dbm: float = float(config.NOISE_THRESHOLD_DEFAULT)
_noise_filter_enabled: bool = True

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

@app.on_event("startup")
async def startup():
    global sdr_manager, classifier, detector, llm_analyzer, learning_engine
    global _tracking_task, _broadcast_task, _oscilloscope_task
    global _antenna_mode, _llm_enabled, _dump1090_start_monotonic
    logger.info("="*80)
    logger.info("🚀 OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR | STARTING...")
    try:
        sdr_manager = SDRReader()
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
        learning_engine = get_learning_engine()
        logger.info("✅ Learning Engine INITIALIZED")
        # Background task: broadcast live aircraft to WebSocket clients
        _broadcast_task = asyncio.create_task(_broadcast_loop())
        _oscilloscope_task = asyncio.create_task(_oscilloscope_loop())
        logger.info("="*80)
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")

@app.on_event("shutdown")
async def shutdown():
    global sdr_manager, llm_analyzer, _tracking_task, _broadcast_task, _oscilloscope_task
    logger.info("🛑 OPHIR 2.0 shutting down...")
    for task in (_broadcast_task, _oscilloscope_task):
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
        # Enrich each aircraft with distance & bearing
        enriched = []
        for ac in aircraft_list:
            ac_copy = dict(ac)
            try:
                dist = estimate_aircraft_distance(ac_copy)
                ac_copy["distance_km"] = dist.get("distance_km")
                ac_copy["bearing_deg"] = dist.get("bearing_deg")
                ac_copy["distance_method"] = dist.get("method")
            except Exception:
                pass
            # Feed civilian aircraft to learning engine
            if learning_engine:
                ac_type = (ac_copy.get("aircraft_type") or "").lower()
                if ac_type in ("civilian", "commercial", "") or not ac_copy.get("is_shadow"):
                    learning_engine.learn_from_aircraft(ac_copy)
            enriched.append(ac_copy)

        payload = json.dumps({"aircraft": enriched, "count": len(enriched)})
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
            for ac in enriched:
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


async def _oscilloscope_loop():
    """Push real-time signal data to oscilloscope WebSocket clients every 200ms."""
    import random  # used only to pad missing samples with noise floor value
    while True:
        await asyncio.sleep(0.2)
        if not _ws_oscilloscope_clients:
            continue
        # CH1: ADS-B RSSI samples from recent signal events
        ch1: list[float] = []
        ch2: list[float] = []
        if sdr_manager:
            noise_hist = list(sdr_manager._noise_history[-60:])
            ch1 = [r.get("noise_dbm", -100) for r in noise_hist]
        # CH2: ambient noise floor (slightly below minimum signal)
        if ch1:
            floor = min(ch1) - 5
            ch2 = [floor + (x % 3) - 1 for x in range(len(ch1))]
        else:
            ch1 = [-100] * 30
            ch2 = [-105] * 30

        payload = json.dumps({
            "ch1": ch1,
            "ch2": ch2,
            "gain_db": _runtime_gain_db,
            "noise_threshold_dbm": _runtime_noise_threshold_dbm,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        dead: list[WebSocket] = []
        for ws in _ws_oscilloscope_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _ws_oscilloscope_clients:
                _ws_oscilloscope_clients.remove(ws)


@app.get("/")
async def root():
    """Serve the main dashboard."""
    dashboard_path = Path(__file__).parent / "web" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
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
    dashboard_path = Path(__file__).parent / "web" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
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

    if llm_analyzer and llm_analyzer.enabled:
        try:
            analysis = await llm_analyzer.analyze_anomaly(hex_code, anomaly_type, aircraft_data)
        except Exception as e:
            logger.error(f"LLM analysis error: {e}")
            analysis = "LLM analysis unavailable"
    elif llm_analyzer and not llm_analyzer.enabled:
        analysis = "LLM analysis is currently disabled. Enable via POST /api/v1/llm/toggle."
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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


# ---------------------------------------------------------------------------
# Button 1: Antenna Mode Switch (GARAGE / AIR)
# ---------------------------------------------------------------------------

@app.put("/api/v1/antenna/mode")
async def set_antenna_mode(body: dict):
    """Switch the active antenna profile at runtime (no restart required).

    Request body: {"mode": "GARAGE" | "AIR"}
    """
    global _antenna_mode

    raw_mode = body.get("mode", "").upper()
    try:
        mode = config.AntennaMode(raw_mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{raw_mode}'. Accepted values: GARAGE, AIR",
        )

    _antenna_mode = mode
    profile = config.ANTENNA_PROFILES[mode]

    if sdr_manager:
        sdr_manager.set_antenna_mode(mode)

    logger.info(
        f"📡 Antenna mode switched to {mode.value} at {datetime.now(timezone.utc).isoformat()}"
    )
    return {
        "antenna_mode": mode.value,
        "rssi_threshold": profile["rssi_threshold"],
        "gain": profile["gain"],
        "description": profile["description"],
        "status": "switched",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/antenna/mode")
async def get_antenna_mode():
    """Return the currently active antenna profile."""
    profile = config.ANTENNA_PROFILES[_antenna_mode]
    return {
        "antenna_mode": _antenna_mode.value,
        "rssi_threshold": profile["rssi_threshold"],
        "gain": profile["gain"],
        "description": profile["description"],
    }


# ---------------------------------------------------------------------------
# Button 2: dump1090 Process Management (ON / OFF / RESTART / STATUS)
# ---------------------------------------------------------------------------

def _get_dump1090_pid() -> int | None:
    """Return PID of a running dump1090 process, or None."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "dump1090"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            pid_str = result.stdout.strip().splitlines()[0]
            return int(pid_str)
    except Exception:
        pass
    return None


def _dump1090_uptime() -> float | None:
    """Return seconds since dump1090 start was last recorded, or None."""
    if _dump1090_start_monotonic is None:
        return None
    return round(time.monotonic() - _dump1090_start_monotonic, 1)


@app.get("/api/v1/dump1090/status")
async def dump1090_status():
    """Return current dump1090 operational status."""
    pid = _get_dump1090_pid()
    running = pid is not None
    aircraft_count = len(sdr_manager.aircraft_dict) if sdr_manager else 0
    last_msg = sdr_manager.get_last_signal_timestamp() if sdr_manager else None

    return {
        "dump1090_status": "running" if running else "stopped",
        "pid": pid,
        "connected": (sdr_manager.connected if sdr_manager else False),
        "uptime_seconds": _dump1090_uptime(),
        "aircraft_count": aircraft_count,
        "last_message": last_msg,
        "error": None,
    }


@app.post("/api/v1/dump1090/start")
async def dump1090_start():
    """Start dump1090 service (uses systemctl if available, otherwise direct)."""
    global _dump1090_start_monotonic

    pid = _get_dump1090_pid()
    if pid:
        return {
            "dump1090_status": "running",
            "pid": pid,
            "action": "already_running",
            "error": None,
        }

    error_msg: str | None = None
    # Try systemctl first, fall back to direct binary
    for cmd in [
        ["systemctl", "start", "dump1090-fa"],
        ["systemctl", "start", "dump1090-mutability"],
        ["systemctl", "start", "dump1090"],
    ]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                _dump1090_start_monotonic = time.monotonic()
                logger.info(f"✅ dump1090 started via: {' '.join(cmd)}")
                return {
                    "dump1090_status": "running",
                    "action": "started",
                    "command": " ".join(cmd),
                    "error": None,
                }
            error_msg = proc.stderr.strip() or proc.stdout.strip()
        except FileNotFoundError:
            continue
        except Exception as e:
            error_msg = str(e)

    logger.error(f"❌ Failed to start dump1090: {error_msg}")
    return {
        "dump1090_status": "stopped",
        "action": "start_failed",
        "error": "Could not start dump1090. Is it installed?",
    }


@app.post("/api/v1/dump1090/stop")
async def dump1090_stop():
    """Stop dump1090 service."""
    global _dump1090_start_monotonic

    pid = _get_dump1090_pid()
    if not pid:
        return {
            "dump1090_status": "stopped",
            "action": "already_stopped",
            "error": None,
        }

    error_msg: str | None = None
    for cmd in [
        ["systemctl", "stop", "dump1090-fa"],
        ["systemctl", "stop", "dump1090-mutability"],
        ["systemctl", "stop", "dump1090"],
    ]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                _dump1090_start_monotonic = None
                logger.info(f"🛑 dump1090 stopped via: {' '.join(cmd)}")
                # Disconnect SDR reader so it stops buffering stale data
                if sdr_manager:
                    sdr_manager.connected = False
                return {
                    "dump1090_status": "stopped",
                    "action": "stopped",
                    "command": " ".join(cmd),
                    "error": None,
                }
            error_msg = proc.stderr.strip() or proc.stdout.strip()
        except FileNotFoundError:
            continue
        except Exception as e:
            error_msg = str(e)

    # As last resort try SIGTERM on the PID
    if pid:
        try:
            subprocess.run(["kill", str(pid)], check=True, timeout=5)
            _dump1090_start_monotonic = None
            if sdr_manager:
                sdr_manager.connected = False
            logger.info(f"🛑 dump1090 (PID {pid}) terminated via SIGTERM")
            return {
                "dump1090_status": "stopped",
                "action": "stopped",
                "command": f"kill {pid}",
                "error": None,
            }
        except Exception as e:
            error_msg = str(e)

    logger.error(f"❌ Failed to stop dump1090: {error_msg}")
    return {
        "dump1090_status": "running",
        "action": "stop_failed",
        "error": "Could not stop dump1090.",
    }


@app.post("/api/v1/dump1090/restart")
async def dump1090_restart():
    """Restart dump1090 service."""
    stop_result = await dump1090_stop()
    await asyncio.sleep(_DUMP1090_RESTART_DELAY)
    start_result = await dump1090_start()
    return {
        "dump1090_status": start_result.get("dump1090_status"),
        "action": "restarted",
        "stop_result": stop_result,
        "start_result": start_result,
        "error": start_result.get("error"),
    }


# ---------------------------------------------------------------------------
# Button 3: LLM Toggle (Enable / Disable AI analysis)
# ---------------------------------------------------------------------------

@app.post("/api/v1/llm/toggle")
async def llm_toggle():
    """Toggle LLM analysis on/off at runtime."""
    global _llm_enabled

    if not llm_analyzer:
        raise HTTPException(status_code=503, detail="LLM analyzer not initialized")

    _llm_enabled = not llm_analyzer.enabled
    llm_analyzer.set_enabled(_llm_enabled)
    action = "enabled" if _llm_enabled else "disabled"
    msg = (
        "LLM analysis enabled. Full AI analysis resumed."
        if _llm_enabled
        else "LLM analysis disabled. Analyze endpoint will return placeholder responses."
    )
    logger.info(f"🤖 LLM {action} at {datetime.now(timezone.utc).isoformat()}")
    return {
        "llm_enabled": _llm_enabled,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": msg,
    }


@app.get("/api/v1/llm/status")
async def llm_status():
    """Return current LLM / Ollama status."""
    if not llm_analyzer:
        return {
            "llm_enabled": False,
            "ollama_connected": False,
            "model": config.OLLAMA_MODEL,
            "response_time_ms": None,
            "last_analysis": None,
        }

    # Refresh connection state without blocking long
    try:
        ollama_ok = await asyncio.wait_for(
            llm_analyzer.check_connection(), timeout=_OLLAMA_STATUS_CHECK_TIMEOUT
        )
    except asyncio.TimeoutError:
        ollama_ok = False

    return {
        "llm_enabled": llm_analyzer.enabled,
        "ollama_connected": ollama_ok,
        "model": llm_analyzer.model,
        "response_time_ms": llm_analyzer.response_time_ms,
        "last_analysis": llm_analyzer.last_analysis,
    }


# ---------------------------------------------------------------------------
# Oscilloscope WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/oscilloscope")
async def ws_oscilloscope(websocket: WebSocket):
    """WebSocket endpoint – streams oscilloscope (signal) data at ~5 Hz."""
    await websocket.accept()
    _ws_oscilloscope_clients.append(websocket)
    logger.info(f"WS /ws/oscilloscope client connected ({len(_ws_oscilloscope_clients)} total)")
    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_oscilloscope_clients:
            _ws_oscilloscope_clients.remove(websocket)
        logger.info("WS /ws/oscilloscope client disconnected")


# ---------------------------------------------------------------------------
# Gain Control
# ---------------------------------------------------------------------------

@app.get("/api/v1/gain/current")
async def gain_current():
    """Return current SDR gain setting."""
    return {
        "gain_db": _runtime_gain_db,
        "gain_min": config.SDR_GAIN_MIN,
        "gain_max": config.SDR_GAIN_MAX,
    }


@app.post("/api/v1/gain/set")
async def gain_set(value: float):
    """Set SDR gain at runtime (dB). Range: SDR_GAIN_MIN … SDR_GAIN_MAX."""
    global _runtime_gain_db
    clamped = max(config.SDR_GAIN_MIN, min(config.SDR_GAIN_MAX, value))
    _runtime_gain_db = clamped
    logger.info(f"🎚️ Gain set to {clamped} dB")
    return {"success": True, "gain_db": clamped}


# ---------------------------------------------------------------------------
# Noise Control
# ---------------------------------------------------------------------------

@app.get("/api/v1/noise/threshold")
async def noise_threshold():
    """Return current noise threshold."""
    return {
        "threshold_dbm": _runtime_noise_threshold_dbm,
        "filter_enabled": _noise_filter_enabled,
        "threshold_min": config.NOISE_THRESHOLD_MIN,
        "threshold_max": config.NOISE_THRESHOLD_MAX,
    }


@app.post("/api/v1/noise/set")
async def noise_set(threshold: float, filter_enabled: bool = True):
    """Set noise threshold and filter state."""
    global _runtime_noise_threshold_dbm, _noise_filter_enabled
    clamped = max(config.NOISE_THRESHOLD_MIN, min(config.NOISE_THRESHOLD_MAX, threshold))
    _runtime_noise_threshold_dbm = clamped
    _noise_filter_enabled = filter_enabled
    logger.info(f"🔊 Noise threshold set to {clamped} dBm, filter={'on' if filter_enabled else 'off'}")
    return {"success": True, "threshold_dbm": clamped, "filter_enabled": filter_enabled}


@app.get("/api/v1/oscilloscope/data")
async def oscilloscope_data():
    """Return a snapshot of the current oscilloscope data (CH1 + CH2)."""
    ch1: list[float] = []
    ch2: list[float] = []
    if sdr_manager:
        noise_hist = list(sdr_manager._noise_history[-60:])
        ch1 = [r.get("noise_dbm", -100) for r in noise_hist]
    if ch1:
        floor = min(ch1) - 5
        ch2 = [floor + (x % 3) - 1 for x in range(len(ch1))]
    else:
        ch1 = [-100] * 30
        ch2 = [-105] * 30
    return {
        "ch1": ch1,
        "ch2": ch2,
        "gain_db": _runtime_gain_db,
        "noise_threshold_dbm": _runtime_noise_threshold_dbm,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# System Status + Shutdown
# ---------------------------------------------------------------------------

@app.get("/api/v1/system/status")
async def system_status():
    """Return full system status."""
    pid = _get_dump1090_pid()
    aircraft_count = len(sdr_manager.aircraft_dict) if sdr_manager else 0
    signal_count = len(sdr_manager._signal_events) if sdr_manager else 0
    uptime_s = _dump1090_uptime()

    if uptime_s is not None:
        h = int(uptime_s // 3600)
        m = int((uptime_s % 3600) // 60)
        s = int(uptime_s % 60)
        uptime_str = f"{h:02d}:{m:02d}:{s:02d}"
    else:
        uptime_str = "N/A"

    return {
        "status": "operational",
        "uptime": uptime_str,
        "uptime_seconds": uptime_s,
        "aircraft_count": aircraft_count,
        "signal_count": signal_count,
        "dump1090_pid": pid,
        "dump1090_running": pid is not None,
        "llm_enabled": llm_analyzer.enabled if llm_analyzer else False,
        "observer_lat": config.OBSERVER_LATITUDE,
        "observer_lon": config.OBSERVER_LONGITUDE,
        "observer_location": config.OBSERVER_LOCATION,
        "gain_db": _runtime_gain_db,
        "noise_threshold_dbm": _runtime_noise_threshold_dbm,
        "learning_samples": learning_engine.get_stats().get("total_civilian_samples", 0) if learning_engine else 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/system/shutdown")
async def system_shutdown():
    """Gracefully shut down the OPHIR server."""
    logger.info("🛑 Graceful shutdown requested via API")
    asyncio.get_event_loop().call_later(1.0, lambda: os.kill(os.getpid(), 15))
    return {"shutdown": "graceful", "message": "Server will stop in 1 second"}


# ---------------------------------------------------------------------------
# Learning Engine Stats
# ---------------------------------------------------------------------------

@app.get("/api/v1/learning/stats")
async def learning_stats():
    """Return learning engine statistics."""
    if not learning_engine:
        return {"status": "not_initialized"}
    return learning_engine.get_stats()


# ---------------------------------------------------------------------------
# Distance / bearing for a specific aircraft
# ---------------------------------------------------------------------------

@app.get("/api/v1/aircraft/{hex_code}/distance")
async def aircraft_distance(hex_code: str):
    """Return distance and bearing for a specific aircraft by ICAO hex code."""
    if not sdr_manager:
        raise HTTPException(status_code=503, detail="SDR not ready")
    ac = sdr_manager.aircraft_dict.get(hex_code.upper())
    if not ac:
        raise HTTPException(status_code=404, detail=f"Aircraft {hex_code} not found")
    return estimate_aircraft_distance(ac)


if __name__ == "__main__":
    logger.info("🚀 Starting OPHIR 2.0 Server")
    logger.info("📡 Listening on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, log_level="info")
