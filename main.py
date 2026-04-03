#!/usr/bin/env python3
"""OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import json
import logging
import math
import subprocess
import time
from datetime import datetime, timezone
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
    global sdr_manager, classifier, detector, llm_analyzer, _tracking_task, _broadcast_task
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


@app.get("/index.html")
@app.get("/archive")
async def get_index():
    """Serve the main archive / dashboard HTML page."""
    for candidate in ["web/index.html", "index.html"]:
        if os.path.exists(candidate):
            return FileResponse(candidate, media_type="text/html")
    raise HTTPException(status_code=404, detail="index.html not found")


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
# Aircraft Details API
# ---------------------------------------------------------------------------

# Observer location (Negev, Israel) used for azimuth/elevation calculations
_OBSERVER_LAT = 31.073541
_OBSERVER_LON = 35.037383
_ADS_B_FREQ_MHZ = 1090.0
# Typical ADS-B EIRP ≈ 250 W → ~54 dBm; 41 dBm gives reasonable range estimates
_ADS_B_EIRP_DBM = 41.0
_FEET_TO_METERS = 0.3048


def _rssi_to_distance_km(rssi_dbm: float) -> float:
    """Estimate distance in km from RSSI using the Friis free-space path loss model.

    FSPL (dB) = 32.45 + 20·log10(f_MHz) + 20·log10(d_km)
    RSSI = EIRP - FSPL  →  d_km = 10^((EIRP - RSSI - 32.45 - 20·log10(f)) / 20)
    """
    fspl = _ADS_B_EIRP_DBM - rssi_dbm
    d_km = 10 ** ((fspl - 32.45 - 20 * math.log10(_ADS_B_FREQ_MHZ)) / 20)
    return round(max(0.1, d_km), 2)


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return bearing from point 1 to point 2 in degrees (0–360)."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlon_r = math.radians(lon2 - lon1)
    x = math.sin(dlon_r) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r)
    bearing = math.degrees(math.atan2(x, y))
    return round((bearing + 360) % 360, 1)


def _elevation_deg(distance_km: float, altitude_m: float) -> float:
    """Estimate elevation angle in degrees given slant distance and aircraft altitude."""
    if distance_km <= 0:
        return 90.0
    alt_km = altitude_m / 1000.0
    return round(math.degrees(math.atan2(alt_km, distance_km)), 1)


def _heading_to_cardinal(heading: float) -> str:
    """Convert heading degrees to cardinal direction abbreviation."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(heading / 45) % 8]


def _build_aircraft_details(hex_code: str, live: dict, db_rec) -> dict:
    """Assemble the full details response from live data and DB record."""
    hex_upper = hex_code.upper()

    # --- position ---
    lat = live.get("latitude")
    lon = live.get("longitude")
    alt_ft = live.get("altitude")  # SBS altitude is in feet
    alt_m = round(alt_ft * _FEET_TO_METERS) if alt_ft is not None else None
    speed_kt = live.get("ground_speed")
    speed_kmh = round(speed_kt * 1.852) if speed_kt is not None else None
    heading = live.get("track")
    rssi = live.get("rssi")
    vertical_rate = live.get("vertical_rate")
    callsign = (live.get("callsign") or "").strip() or None

    # --- signal ---
    distance_km = _rssi_to_distance_km(rssi) if rssi is not None else None
    azimuth = None
    elevation = None
    if lat is not None and lon is not None:
        azimuth = _bearing_deg(_OBSERVER_LAT, _OBSERVER_LON, lat, lon)
    if distance_km is not None and alt_m is not None:
        elevation = _elevation_deg(distance_km, alt_m)

    # Signal quality heuristic (0–100)
    signal_quality = None
    if rssi is not None:
        # Map [-100 dBm, -50 dBm] linearly to [0, 100]
        signal_quality = max(0, min(100, int((rssi + 100) * 2)))

    # --- DB record fields (may be None) ---
    db_callsign = None
    db_type = None
    db_country = None
    db_in_db = False
    db_first_logged = None
    db_suspicious = False
    db_notes = None
    db_first_seen = None
    db_last_seen = None

    if db_rec:
        db_in_db = True
        db_callsign = db_rec.callsign
        db_type = db_rec.aircraft_type
        db_country = db_rec.country
        db_suspicious = bool(getattr(db_rec, "suspicious", False))
        db_notes = getattr(db_rec, "user_notes", None)
        db_first_seen = db_rec.first_seen.isoformat() if db_rec.first_seen else None
        db_last_seen = db_rec.last_seen.isoformat() if db_rec.last_seen else None
        db_first_logged = db_rec.first_seen.strftime("%Y-%m-%d") if db_rec.first_seen else None

    effective_callsign = callsign or db_callsign
    effective_type = db_type
    effective_country = db_country

    # --- basic classification heuristics ---
    threat_level = "NONE"
    aircraft_class = "Unknown"
    if db_in_db:
        aircraft_class = "Civilian Commercial"
        threat_level = "NONE"
    elif not effective_callsign:
        aircraft_class = "Unknown"
        threat_level = "UNKNOWN"
    else:
        aircraft_class = "Unregistered"
        threat_level = "LOW"

    gps_transmitting = lat is not None and lon is not None

    return {
        "icao_hex": hex_upper,
        "callsign": effective_callsign,
        "country": effective_country,
        "registration": None,
        "airline": None,

        "aircraft": {
            "type": effective_type,
            "model": None,
            "manufacturer": None,
            "engines": None,
            "engine_type": None,
            "max_altitude": None,
            "cruise_speed": None,
        },

        "position": {
            "latitude": lat,
            "longitude": lon,
            "altitude_ft": alt_ft,
            "altitude_m": alt_m,
            "speed_kt": speed_kt,
            "speed_kmh": speed_kmh,
            "heading": heading,
            "heading_cardinal": _heading_to_cardinal(heading) if heading is not None else None,
            "vertical_rate": vertical_rate,
            "timestamp": live.get("last_seen"),
        },

        "signal": {
            "rssi_dbm": rssi,
            "signal_quality": signal_quality,
            "distance_km": distance_km,
            "distance_method": "Friis Free Space Loss (1090 MHz)" if rssi is not None else None,
            "observer_lat": _OBSERVER_LAT,
            "observer_lon": _OBSERVER_LON,
            "azimuth": azimuth,
            "elevation": elevation,
        },

        "classification": {
            "aircraft_class": aircraft_class,
            "threat_level": threat_level,
            "gps_transmitting": gps_transmitting,
            "military_suspect": False,
            "jammer_detected": False,
            "spoofing_detected": False,
        },

        "tracking": {
            "first_seen": db_first_seen or live.get("first_seen"),
            "last_seen": db_last_seen or live.get("last_seen"),
            "message_count": live.get("messages"),
        },

        "database": {
            "in_database": db_in_db,
            "first_logged": db_first_logged,
            "suspicious": db_suspicious,
            "user_notes": db_notes,
        },
    }


@app.get("/api/v1/aircraft/{hex_code}/details")
async def get_aircraft_details(hex_code: str):
    """Return detailed information for a single aircraft by ICAO hex code.

    Combines live tracking data with the database record.  A 404 is returned
    only when the aircraft is neither in the live tracking dict nor in the DB.
    """
    hex_upper = hex_code.upper()

    # Live data from SDR tracking loop
    live: dict = {}
    if sdr_manager:
        live = sdr_manager.aircraft_dict.get(hex_upper, {})

    # Database record
    db_rec = None
    try:
        session = db_manager.get_sync_session()
        try:
            db_rec = db_manager.get_aircraft_by_hex(session, hex_upper)
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"DB lookup failed for {hex_upper}: {e}")

    if not live and db_rec is None:
        raise HTTPException(status_code=404, detail=f"Aircraft {hex_upper} not found")

    details = _build_aircraft_details(hex_upper, live, db_rec)

    # Attempt LLM classification if available
    if llm_analyzer and llm_analyzer.enabled and live:
        try:
            llm_text = await asyncio.wait_for(
                llm_analyzer.analyze_anomaly(hex_upper, "DETAILS_REQUEST", live),
                timeout=10.0,
            )
            details["llm_analysis"] = {"description": llm_text, "confidence": None}
        except Exception:
            details["llm_analysis"] = {"description": "LLM analysis unavailable.", "confidence": None}
    else:
        details["llm_analysis"] = {"description": None, "confidence": None}

    return details


@app.get("/api/v1/aircraft/{hex_code}/position")
async def get_aircraft_position(hex_code: str):
    """Return the latest position snapshot for a single aircraft (lightweight)."""
    hex_upper = hex_code.upper()
    if not sdr_manager:
        raise HTTPException(status_code=503, detail="SDR manager not ready")
    live = sdr_manager.aircraft_dict.get(hex_upper)
    if not live:
        raise HTTPException(status_code=404, detail=f"Aircraft {hex_upper} not tracked")
    alt_ft = live.get("altitude")
    speed_kt = live.get("ground_speed")
    return {
        "icao_hex": hex_upper,
        "latitude": live.get("latitude"),
        "longitude": live.get("longitude"),
        "altitude_ft": alt_ft,
        "altitude_m": round(alt_ft * _FEET_TO_METERS) if alt_ft is not None else None,
        "speed_kt": speed_kt,
        "speed_kmh": round(speed_kt * 1.852) if speed_kt is not None else None,
        "heading": live.get("track"),
        "rssi_dbm": live.get("rssi"),
        "timestamp": live.get("last_seen"),
    }


@app.post("/api/v1/aircraft/unknown/add")
async def add_unknown_aircraft(body: dict):
    """Add an unknown aircraft to the database.

    Request body:
        icao_hex        (required) – 6-character ICAO hex code
        aircraft_type   – e.g. "Boeing 737-800"
        callsign        – e.g. "SU123"
        country         – ISO-3 or full country name
        aircraft_class  – "Civilian Commercial", "Military", "UAV/Drone", etc.
        description     – free-text description
        user_notes      – operator notes
    """
    hex_code = (body.get("icao_hex") or "").strip().upper()
    if not hex_code:
        raise HTTPException(status_code=400, detail="icao_hex is required")
    if not (len(hex_code) == 6 and all(c in "0123456789ABCDEF" for c in hex_code)):
        raise HTTPException(status_code=400, detail="icao_hex must be exactly 6 hexadecimal characters")

    aircraft_data = {
        "hex_code": hex_code,
        "callsign": (body.get("callsign") or "").strip() or None,
        "aircraft_type": body.get("aircraft_type"),
        "country": body.get("country"),
    }

    try:
        session = db_manager.get_sync_session()
        try:
            ok = db_manager.add_aircraft(session, aircraft_data)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to add unknown aircraft {hex_code}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save aircraft to database")

    logger.info(f"✅ Unknown aircraft {hex_code} added to database")
    return {
        "success": True,
        "message": "Aircraft added to database",
        "hex_code": hex_code,
    }


if __name__ == "__main__":
    logger.info("🚀 Starting OPHIR 2.0 Server")
    logger.info("📡 Listening on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, log_level="info")
