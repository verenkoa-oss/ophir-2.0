from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
"""
OPHIR 2.0 | AEGIS-X Main Application
FastAPI server for ADS-B tracking with SDR, DB, and LLM integration
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import logging
from datetime import datetime
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

import config
from core.sdr import SDRReader
from core.database import db_manager
from core.llm import LLMAnalyzer
from db.schema import Aircraft

# ==================== LOGGING ====================
logging.basicConfig(
    level=config.LOG_LEVEL,
    format=config.LOG_FORMAT,
    handlers=[
        logging.FileHandler(config.LOGS_DIR / "ophir.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== FASTAPI SETUP ====================
app = FastAPI(
    title="OPHIR 2.0 | AEGIS-X",
    description="ADS-B Tracking with SDR, Shadow Detection & LLM Analysis",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)

# ==================== GLOBAL STATE ====================
sdr = None
llm = None
active_aircraft = {}
background_tasks = []

# ==================== STARTUP / SHUTDOWN ====================
@app.on_event("startup")
async def startup():
    """Initialize SDR, DB, and LLM on startup"""
    global sdr, llm
    
    logger.info("🚀 OPHIR 2.0 STARTUP")
    logger.info(f"Config: {config.API_HOST}:{config.API_PORT}")
    
    # Initialize LLM
    if config.ENABLE_LLM_ANALYSIS:
        llm = LLMAnalyzer()
        await llm.init()
    
    # Start SDR reader task
    if config.ENABLE_SHADOW_TRACKING:
        sdr = SDRReader()
        task = asyncio.create_task(sdr_reader_task())
        background_tasks.append(task)
        logger.info("✅ SDR reader started")
    
    logger.info("✅ OPHIR startup complete")

@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    global sdr, llm
    
    logger.info("🛑 OPHIR SHUTDOWN")
    
    if sdr:
        await sdr.close()
    if llm:
        await llm.close()
    
    for task in background_tasks:
        task.cancel()
    
    db_manager.close()
    logger.info("✅ Cleanup complete")

# ==================== BACKGROUND TASKS ====================
async def sdr_reader_task():
    """Background task: read from SDR continuously"""
    try:
        await sdr.connect()
        async for msg in sdr.read_messages():
            if msg:
                await process_aircraft_message(msg)
    except Exception as e:
        logger.error(f"SDR reader task error: {e}")
        await asyncio.sleep(5)

async def process_aircraft_message(msg):
    """Process incoming aircraft message"""
    try:
        hex_code = msg['hex_code']
        session = db_manager.get_sync_session()
        
        # Update aircraft in DB
        db_manager.add_aircraft(session, msg)
        
        # Check for anomalies
        if config.ENABLE_SHADOW_TRACKING:
            await check_anomalies(session, hex_code, msg)
        
        session.close()
        
        # Update in-memory tracking
        active_aircraft[hex_code] = msg
    
    except Exception as e:
        logger.error(f"Error processing message: {e}")

async def check_anomalies(session, hex_code: str, aircraft_data: dict):
    """Detect anomalies (shadow targets, unusual patterns)"""
    try:
        rssi = aircraft_data.get('rssi')
        
        # Detect shadow targets (very weak signals)
        if rssi and rssi < config.SHADOW_RSSI_THRESHOLD:
            db_manager.add_anomaly(
                session,
                hex_code,
                "SHADOW_TARGET",
                f"Weak RSSI: {rssi} dBm (below {config.SHADOW_RSSI_THRESHOLD})"
            )
            
            # LLM analysis
            if config.ENABLE_LLM_ANALYSIS and llm:
                analysis = await llm.analyze_anomaly(
                    hex_code,
                    "SHADOW_TARGET",
                    aircraft_data
                )
                logger.info(f"Shadow analysis for {hex_code}: {analysis[:50]}...")
    
    except Exception as e:
        logger.error(f"Anomaly check error: {e}")

# ==================== API ENDPOINTS ====================

@app.get("/")
async def root():
    """API root"""
    return {
        "name": "OPHIR 2.0 | AEGIS-X",
        "version": "2.0.0",
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "healthy",
        "sdr_connected": sdr.connected if sdr else False,
        "llm_available": llm is not None,
        "active_aircraft": len(active_aircraft),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/aircraft")
async def get_all_aircraft():
    """Get all active aircraft"""
    try:
        session = db_manager.get_sync_session()
        aircraft = session.query(Aircraft).all()
        session.close()
        
        return {
            "count": len(aircraft),
            "aircraft": [
                {
                    "hex_code": a.hex_code,
                    "callsign": a.callsign,
                    "altitude": a.altitude,
                    "latitude": a.latitude,
                    "longitude": a.longitude,
                    "rssi": a.rssi,
                    "is_shadow": a.is_shadow
                }
                for a in aircraft
            ]
        }
    except Exception as e:
        logger.error(f"Error fetching aircraft: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/aircraft/{hex_code}")
async def get_aircraft(hex_code: str):
    """Get specific aircraft details"""
    try:
        session = db_manager.get_sync_session()
        aircraft = db_manager.get_aircraft_by_hex(session, hex_code.upper())
        session.close()
        
        if not aircraft:
            raise HTTPException(status_code=404, detail="Aircraft not found")
        
        return {
            "hex_code": aircraft.hex_code,
            "callsign": aircraft.callsign,
            "altitude": aircraft.altitude,
            "ground_speed": aircraft.ground_speed,
            "latitude": aircraft.latitude,
            "longitude": aircraft.longitude,
            "rssi": aircraft.rssi,
            "is_shadow": aircraft.is_shadow,
            "first_seen": aircraft.first_seen,
            "last_seen": aircraft.last_seen
        }
    except Exception as e:
        logger.error(f"Error fetching aircraft: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stats")
async def get_stats():
    """Get system statistics"""
    try:
        session = db_manager.get_sync_session()
        total_aircraft = session.query(Aircraft).count()
        shadow_count = session.query(Aircraft).filter_by(is_shadow=True).count()
        session.close()
        
        return {
            "total_aircraft": total_aircraft,
            "shadow_targets": shadow_count,
            "active_in_memory": len(active_aircraft),
            "sdr_enabled": config.ENABLE_SHADOW_TRACKING,
            "llm_enabled": config.ENABLE_LLM_ANALYSIS,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== WEBSOCKET ====================
@app.websocket("/ws/aircraft")
async def websocket_aircraft(websocket: WebSocket):
    """WebSocket for real-time aircraft updates"""
    await websocket.accept()
    logger.info("WebSocket client connected")
    
    try:
        while True:
            await asyncio.sleep(1)
            await websocket.send_json({
                "type": "aircraft_update",
                "count": len(active_aircraft),
                "timestamp": datetime.utcnow().isoformat()
            })
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await websocket.close()
        logger.info("WebSocket client disconnected")

# ==================== MAIN ====================

# API Routes
@app.get("/api/v1/archive/aircraft")
async def get_archive_aircraft():
    import sqlite3
    try:
        conn = sqlite3.connect('db/ophir.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM aircraft_archive')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error: {e}")
        return []


# Serve static files
from fastapi.responses import FileResponse
import os

@app.get("/web/{file_path:path}")
async def serve_web(file_path: str):
    file_path = file_path or "index.html"
    full_path = os.path.join("web", file_path)
    
    if os.path.isfile(full_path):
        return FileResponse(full_path)
    
    # Fallback to index.html for SPA routing
    if os.path.isfile("web/index.html"):
        return FileResponse("web/index.html")
    
    return {"detail": "Not Found"}

if __name__ == "__main__":
    logger.info("Starting OPHIR 2.0")
    
    uvicorn.run(
        app,
        host=config.API_HOST,
        port=config.API_PORT,
        workers=1,
        log_level=config.API_LOG_LEVEL.lower()
    )


# Serve static files


