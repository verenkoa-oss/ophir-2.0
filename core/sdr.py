"""
OPHIR SDR Module
Reads ADS-B data from dump1090 via TCP port 30005
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import logging
from datetime import datetime
import config

logger = logging.getLogger(__name__)


class SDRReader:
    """Read ADS-B messages from dump1090"""
    
    def __init__(self):
        self.host = config.DUMP1090_HOST
        self.port = config.DUMP1090_PORT
        self.reader = None
        self.writer = None
        self.connected = False
        self.aircraft_data = {}
    
    async def connect(self):
        """Connect to dump1090 TCP port"""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.connected = True
            logger.info(f"✅ Connected to dump1090 at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"❌ Failed to connect to dump1090: {e}")
            self.connected = False
            raise
    
    async def read_messages(self):
        """Read SBS format messages from dump1090"""
        if not self.connected:
            await self.connect()
        
        try:
            while self.connected:
                try:
                    data = await asyncio.wait_for(
                        self.reader.read(1024), timeout=5.0
                    )
                    if not data:
                        logger.warning("Connection closed by dump1090")
                        self.connected = False
                        break
                    
                    messages = data.decode('utf-8', errors='ignore').strip().split('\n')
                    for msg in messages:
                        if msg.startswith('MSG'):
                            yield self.parse_sbs_message(msg)
                
                except asyncio.TimeoutError:
                    logger.debug("No data from dump1090 (timeout)")
                    continue
                except Exception as e:
                    logger.error(f"Error reading from dump1090: {e}")
                    self.connected = False
                    break
        
        except Exception as e:
            logger.error(f"Fatal error in read_messages: {e}")
            self.connected = False
    
    def parse_sbs_message(self, msg):
        """Parse SBS format message from dump1090
        Format: MSG,1,111,11111,hex,111,2015/04/14,12:34:56.123,2015/04/14,12:34:56.123,callsign,102400,180,265,1.5,-14.6,0,,0,0
        """
        try:
            parts = msg.split(',')
            if len(parts) < 15:
                return None
            
            return {
                'hex_code': parts[4].upper(),
                'callsign': parts[10].strip() if len(parts) > 10 else None,
                'altitude': float(parts[11]) if parts[11] else None,
                'ground_speed': float(parts[12]) if parts[12] else None,
                'track': float(parts[13]) if parts[13] else None,
                'latitude': float(parts[14]) if parts[14] else None,
                'longitude': float(parts[15]) if parts[15] else None,
                'rssi': float(parts[16]) if len(parts) > 16 and parts[16] else None,
                'timestamp': datetime.utcnow()
            }
        except Exception as e:
            logger.debug(f"Failed to parse SBS message: {e}")
            return None
    
    async def close(self):
        """Close connection"""
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        self.connected = False
        logger.info("SDR connection closed")

async def test_sdr():
    """Test SDR connection"""
    sdr = SDRReader()
    try:
        await sdr.connect()
        logger.info("Testing SDR reader...")
        
        count = 0
        async for msg in sdr.read_messages():
            if msg:
                logger.info(f"Aircraft: {msg['hex_code']} | Callsign: {msg['callsign']}")
                count += 1
                if count >= 5:
                    break
    except Exception as e:
        logger.error(f"SDR test failed: {e}")
    finally:
        await sdr.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_sdr())
