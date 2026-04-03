"""
OPHIR Database Manager
Async SQLAlchemy session management
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import logging
import config
from db.schema import Base, Aircraft, TrackHistory, AnomalyLog

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manage SQLite connections and sessions"""
    
    def __init__(self):
        self.engine = None
        self.async_engine = None
        self.SessionLocal = None
        self.AsyncSessionLocal = None
    
    def init_sync(self):
        """Initialize synchronous engine (for sync code)"""
        try:
            self.engine = create_engine(
                config.DATABASE_URL,
                echo=config.DB_ECHO,
                connect_args={"check_same_thread": False},
                pool_size=config.DB_POOL_SIZE,
                max_overflow=config.DB_MAX_OVERFLOW
            )
            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )
            Base.metadata.create_all(self.engine)
            logger.info("✅ Sync database initialized")
        except Exception as e:
            logger.error(f"❌ Failed to init sync DB: {e}")
            raise
    
    def get_sync_session(self) -> Session:
        """Get synchronous DB session"""
        if not self.SessionLocal:
            self.init_sync()
        return self.SessionLocal()
    
    async def init_async(self):
        """Initialize async engine (for async code)"""
        try:
            self.async_engine = create_async_engine(
                config.DATABASE_URL.replace("sqlite://", "sqlite+aiosqlite:///"),
                echo=config.DB_ECHO,
                connect_args={"check_same_thread": False}
            )
            self.AsyncSessionLocal = async_sessionmaker(
                self.async_engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            logger.info("✅ Async database initialized")
        except Exception as e:
            logger.error(f"❌ Failed to init async DB: {e}")
            raise
    
    async def get_async_session(self) -> AsyncSession:
        """Get async DB session"""
        if not self.AsyncSessionLocal:
            await self.init_async()
        return self.AsyncSessionLocal()
    
    def add_aircraft(self, session: Session, aircraft_data: dict):
        """Add or update aircraft in DB"""
        try:
            hex_code = aircraft_data['hex_code']
            existing = session.query(Aircraft).filter_by(hex_code=hex_code).first()
            
            if existing:
                for key, value in aircraft_data.items():
                    setattr(existing, key, value)
            else:
                aircraft = Aircraft(**aircraft_data)
                session.add(aircraft)
            
            session.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding aircraft: {e}")
            session.rollback()
            return False
    
    def add_track_history(self, session: Session, hex_code: str, track_data: dict):
        """Log aircraft position to history"""
        try:
            history = TrackHistory(hex_code=hex_code, **track_data)
            session.add(history)
            session.commit()
            return True
        except Exception as e:
            logger.error(f"Error logging track history: {e}")
            session.rollback()
            return False
    
    def add_anomaly(self, session: Session, hex_code: str, anomaly_type: str, description: str):
        """Log anomaly for LLM analysis"""
        try:
            anomaly = AnomalyLog(
                hex_code=hex_code,
                anomaly_type=anomaly_type,
                description=description
            )
            session.add(anomaly)
            session.commit()
            return True
        except Exception as e:
            logger.error(f"Error logging anomaly: {e}")
            session.rollback()
            return False
    
    def get_all_aircraft(self, session: Session):
        """Get all active aircraft"""
        return session.query(Aircraft).all()
    
    def get_aircraft_by_hex(self, session: Session, hex_code: str):
        """Get specific aircraft"""
        return session.query(Aircraft).filter_by(hex_code=hex_code).first()
    
    def close(self):
        """Close database connections"""
        if self.engine:
            self.engine.dispose()
        logger.info("Database connections closed")

# Global instance
db_manager = DatabaseManager()
db_manager.init_sync()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("✅ Database manager initialized")
