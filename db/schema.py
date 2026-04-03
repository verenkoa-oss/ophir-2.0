"""
OPHIR Database Schema
SQLite ORM models for aircraft tracking and history.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, Index
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import config

Base = declarative_base()

class Aircraft(Base):
    """Active aircraft tracking table"""
    __tablename__ = "aircraft"
    
    id = Column(Integer, primary_key=True)
    hex_code = Column(String(6), unique=True, index=True)
    callsign = Column(String(10), nullable=True)
    aircraft_type = Column(String(50), nullable=True)
    country = Column(String(3), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    altitude = Column(Float, nullable=True)
    ground_speed = Column(Float, nullable=True)
    track = Column(Float, nullable=True)
    rssi = Column(Float, nullable=True)
    
    is_shadow = Column(Boolean, default=False)
    estimated_distance = Column(Float, nullable=True)
    
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_hex_lastseen', 'hex_code', 'last_seen'),
        Index('idx_shadow', 'is_shadow'),
    )

class TrackHistory(Base):
    """Historical positions for track playback"""
    __tablename__ = "track_history"
    
    id = Column(Integer, primary_key=True)
    hex_code = Column(String(6), index=True)
    latitude = Column(Float)
    longitude = Column(Float)
    altitude = Column(Float)
    rssi = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    
    __table_args__ = (
        Index('idx_hex_time', 'hex_code', 'timestamp'),
    )

class AnomalyLog(Base):
    """Detected anomalies for LLM analysis"""
    __tablename__ = "anomalies"
    
    id = Column(Integer, primary_key=True)
    hex_code = Column(String(6), index=True)
    anomaly_type = Column(String(50))
    description = Column(String(500))
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    llm_analysis = Column(String(2000), nullable=True)
    
    __table_args__ = (
        Index('idx_anomaly_time', 'detected_at'),
    )

def init_db():
    """Initialize database and create tables"""
    engine = create_engine(config.DATABASE_URL, echo=config.DB_ECHO)
    Base.metadata.create_all(engine)
    return engine

if __name__ == "__main__":
    init_db()
    print("✅ Database schema initialized")
