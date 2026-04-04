#!/usr/bin/env python3
"""OPHIR 2.0 | AEGIS-X AIRSPACE MONITOR"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import collections
import json
import logging
import math
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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

# Serve static web assets (CSS, JS, HTML) from /web directory
_web_dir = Path(__file__).parent / "web"
if _web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(_web_dir)), name="web")

from core.sdr_real import SDRReader
from core.signal_classifier import get_classifier
from core.threat_detector import get_detector
from core.database import db_manager
from core.llm import LLMAnalyzer
from core.distance_calculator import estimate_distance
from core.learning_engine import get_learning_engine
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
_runtime_gain_db: float = float(config.SDR_GAIN_DEFAULT)

# Noise threshold / filter settings
_noise_threshold_dbm: float = float(config.NOISE_THRESHOLD_DEFAULT)
_runtime_noise_threshold_dbm: float = float(config.NOISE_THRESHOLD_DEFAULT)
_noise_filter_enabled: bool = True
_noise_alert_enabled: bool = True
_noise_show_events: bool = True
_noise_spike_events: list = []  # {dbm, ts} dicts for the last 5 min

# System runtime
_system_start_monotonic: float = time.monotonic()
_signals_processed: int = 0


def _get_dump1090_pid() -> int | None:
    """Return the PID of a running dump1090-basic (mutability) process, or None."""
    for binary in ("dump1090", "dump1090-mutability"):
        try:
            result = subprocess.run(
                ["pgrep", "-x", binary],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().split()[0])
        except Exception:
            pass
    return None


def _dump1090_uptime() -> float | None:
    """Return seconds since dump1090 was last confirmed running, or None."""
    if _dump1090_start_monotonic is None:
        return None
    return time.monotonic() - _dump1090_start_monotonic

@app.on_event("startup")
async def startup():
    global sdr_manager, classifier, detector, llm_analyzer, learning_engine
    global _tracking_task, _broadcast_task
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
        learning_engine = get_learning_engine()
        await learning_engine.start()
        logger.info("✅ Learning Engine STARTED")
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
    global sdr_manager, llm_analyzer, learning_engine, _tracking_task, _broadcast_task, _osc_task
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
        await sdr_manager.close()
    if llm_analyzer:
        await llm_analyzer.close()
    if learning_engine:
        await learning_engine.stop()


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
            # Feed civilian aircraft (with GPS) into the learning engine
            if learning_engine:
                if ac_copy.get("latitude") is not None and ac_copy.get("longitude") is not None:
                    try:
                        learning_engine.learn_from_aircraft(ac_copy)
                    except Exception:
                        pass
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
    """Serve dashboard if available, otherwise return API info."""
    dashboard_path = Path(__file__).parent / "web" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
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
    dashboard_path = Path(__file__).parent / "web" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
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
        if not llm_analyzer:
            return {
                "hex_code": req.hex_code,
                "analysis": "LLM not initialized",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        raw = await llm_analyzer.analyze_anomaly(
            req.hex_code,
            req.anomaly_type,
            req.aircraft_data or {},
        )
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


# ---------------------------------------------------------------------------
# Distance calculation endpoint
# ---------------------------------------------------------------------------

@app.post("/api/v1/distance")
async def calculate_distance(body: dict):
    """Estimate aircraft distance from RSSI or GPS coordinates.

    Request body (all fields optional):
        rssi_dbm    – received signal strength (dBm)
        latitude    – aircraft latitude (decimal degrees)
        longitude   – aircraft longitude (decimal degrees)
    """
    rssi = body.get("rssi_dbm")
    lat = body.get("latitude")
    lon = body.get("longitude")

    result = estimate_distance(rssi, lat, lon)

    # Enhance estimate with calibrated model if only RSSI available
    if result["method"] == "rssi" and rssi is not None and learning_engine:
        calibrated = learning_engine.calibrated_distance_km(float(rssi))
        result["distance_km_calibrated"] = calibrated
    else:
        result["distance_km_calibrated"] = result.get("distance_km")

    result["observer_lat"] = config.OBSERVER_LAT
    result["observer_lon"] = config.OBSERVER_LON
    return result


# ---------------------------------------------------------------------------
# Learning engine endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/learning/stats")
async def learning_stats():
    """Return learning engine statistics (training sample count, calibration)."""
    if not learning_engine:
        return {"error": "Learning engine not initialized"}
    return learning_engine.stats()


@app.get("/api/v1/learning/samples")
async def learning_samples(limit: int = 50):
    """Return recent training samples collected from civilian aircraft."""
    if not learning_engine:
        return {"samples": [], "count": 0}
    samples = learning_engine.recent_samples(limit=min(limit, 200))
    return {"samples": samples, "count": len(samples)}


# ---------------------------------------------------------------------------
# Aircraft classification endpoint (per-aircraft LLM analysis)
# ---------------------------------------------------------------------------

@app.get("/api/v1/aircraft/{hex_code}/analysis")
async def aircraft_analysis(hex_code: str):
    """Return classification and LLM analysis for a specific aircraft."""
    hex_code = hex_code.upper()
    if not sdr_manager:
        raise HTTPException(status_code=503, detail="SDR not initialized")

    aircraft_data = sdr_manager.aircraft_dict.get(hex_code)
    if not aircraft_data:
        raise HTTPException(status_code=404, detail=f"Aircraft {hex_code} not in live data")

    # Distance estimate
    dist_info = estimate_distance(
        aircraft_data.get("rssi"),
        aircraft_data.get("latitude"),
        aircraft_data.get("longitude"),
    )
    if dist_info["method"] == "rssi" and aircraft_data.get("rssi") is not None and learning_engine:
        dist_info["distance_km_calibrated"] = learning_engine.calibrated_distance_km(
            float(aircraft_data["rssi"])
        )

    # Classification
    classification = (
        classifier.classify(aircraft_data)
        if classifier
        else {"category": "UNKNOWN", "confidence": 0.0, "reason": "classifier not ready"}
    )

    # LLM analysis (non-blocking: skip if disabled or unavailable)
    llm_result = "LLM analysis not requested"
    if llm_analyzer and llm_analyzer.enabled:
        anomaly_type = classification.get("category", "GENERAL")
        try:
            llm_result = await asyncio.wait_for(
                llm_analyzer.analyze_anomaly(hex_code, anomaly_type, aircraft_data),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            llm_result = "LLM analysis timed out"
        except Exception as exc:
            logger.error(f"LLM analysis error for {hex_code}: {exc}")
            llm_result = "LLM analysis error"

    return {
        "hex_code": hex_code,
        "aircraft": aircraft_data,
        "classification": classification,
        "distance": dist_info,
        "llm_analysis": llm_result,
        "observer": {"lat": config.OBSERVER_LAT, "lon": config.OBSERVER_LON},
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
        noise_floor = min(ch1) - 5
        ch2 = [noise_floor + (x % 3) - 1 for x in range(len(ch1))]
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


# ---------------------------------------------------------------------------
# dump1090 Management  (basic / mutability – NOT dump1090-fa)
# ---------------------------------------------------------------------------

@app.get("/api/v1/dump1090/status")
async def dump1090_status_endpoint():
    """Return running status of the dump1090-basic process."""
    pid = _get_dump1090_pid()
    uptime_s = _dump1090_uptime()
    return {
        "dump1090_status": "running" if pid else "stopped",
        "pid": pid,
        "uptime_seconds": uptime_s,
    }


@app.post("/api/v1/dump1090/start")
async def dump1090_start():
    """Start dump1090 basic (--net) if it is not already running."""
    global _dump1090_start_monotonic
    if _get_dump1090_pid():
        return {"success": False, "message": "dump1090 is already running"}
    try:
        subprocess.Popen(
            ["dump1090", "--net", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(1.2)
        pid = _get_dump1090_pid()
        if pid:
            _dump1090_start_monotonic = time.monotonic()
            logger.info(f"✅ dump1090 started (PID {pid})")
            return {"success": True, "pid": pid}
        return {"success": False, "message": "dump1090 did not start – check binary path"}
    except FileNotFoundError:
        return {"success": False, "message": "dump1090 binary not found in PATH"}
    except Exception as e:
        logger.error(f"dump1090 start error: {e}")
        return {"success": False, "message": "Failed to start dump1090 – see server log"}


@app.post("/api/v1/dump1090/stop")
async def dump1090_stop():
    """Send SIGTERM to the running dump1090 process."""
    global _dump1090_start_monotonic
    pid = _get_dump1090_pid()
    if not pid:
        return {"success": False, "message": "dump1090 is not running"}
    try:
        import signal as _signal
        os.kill(pid, _signal.SIGTERM)
        _dump1090_start_monotonic = None
        logger.info(f"🛑 dump1090 stopped (PID {pid})")
        return {"success": True, "stopped_pid": pid}
    except Exception as e:
        logger.error(f"dump1090 stop error: {e}")
        return {"success": False, "message": "Failed to stop dump1090 – see server log"}


@app.post("/api/v1/dump1090/restart")
async def dump1090_restart():
    """Stop then start dump1090 basic."""
    stop_result = await dump1090_stop()
    await asyncio.sleep(_DUMP1090_RESTART_DELAY)
    start_result = await dump1090_start()
    return {"stop": stop_result, "start": start_result}


# ---------------------------------------------------------------------------
# LLM Control
# ---------------------------------------------------------------------------

@app.get("/api/v1/llm/status")
async def llm_status_endpoint():
    """Return LLM enabled state and Ollama connectivity."""
    if not llm_analyzer:
        return {"llm_enabled": False, "ollama_connected": False, "model": None}
    # Lightweight connectivity check only when LLM is enabled
    connected = llm_analyzer.ollama_connected
    if llm_analyzer.enabled:
        connected = await llm_analyzer.check_connection()
    return {
        "llm_enabled": llm_analyzer.enabled,
        "ollama_connected": connected,
        "model": config.OLLAMA_MODEL,
    }


@app.post("/api/v1/llm/toggle")
async def llm_toggle():
    """Toggle LLM analysis on/off at runtime."""
    global _llm_enabled
    if not llm_analyzer:
        raise HTTPException(status_code=503, detail="LLM not initialized")
    _llm_enabled = not llm_analyzer.enabled
    llm_analyzer.set_enabled(_llm_enabled)
    logger.info(f"🤖 LLM toggled → {'enabled' if _llm_enabled else 'disabled'}")
    return {"llm_enabled": _llm_enabled}


# ---------------------------------------------------------------------------
# Antenna Mode Control
# ---------------------------------------------------------------------------

class _AntennaModeBody(BaseModel):
    mode: config.AntennaMode


@app.put("/api/v1/antenna/mode")
async def set_antenna_mode(body: _AntennaModeBody):
    """Switch antenna profile between GARAGE and AIR."""
    global _antenna_mode
    _antenna_mode = body.mode
    if sdr_manager:
        sdr_manager.set_antenna_mode(body.mode)
    logger.info(f"📡 Antenna mode → {body.mode}")
    return {"antenna_mode": body.mode, "success": True}


if __name__ == "__main__":
    logger.info("🚀 Starting OPHIR 2.0 Server")
    logger.info(f"📡 Observer location: {config.OBSERVER_LAT}, {config.OBSERVER_LON}")
    logger.info("📡 Listening on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, log_level="info")
