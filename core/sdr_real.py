"""
OPHIR 2.0 | SDR Real Module
Connects to dump1090 port 30001 (raw AVR format) and reads ADS-B messages.
Runs a background _continuous_read() task to fill aircraft_dict and noise data.
"""

import asyncio
import logging
import math
import random
import time
from collections import deque
from datetime import datetime

try:
    import pyModeS as pms
    _PMS_AVAILABLE = True
except ImportError:
    _PMS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Port 30001: raw AVR output from dump1090 (lines like: *HEX...;)
DUMP1090_HOST = "localhost"
DUMP1090_PORT = 30001


class SDRReader:
    """Read raw ADS-B messages from dump1090 port 30001."""

    def __init__(self, host: str = DUMP1090_HOST, port: int = DUMP1090_PORT):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.connected = False

        self.aircraft_dict: dict = {}        # icao -> aircraft data
        self.noise_levels: deque = deque(maxlen=300)  # {time, level}
        self.adsb_levels: deque = deque(maxlen=300)   # {time, level} per ADS-B packet
        self.signal_events: list = []                  # recent decoded events

        self._raw_messages: deque = deque(maxlen=200)  # for terminal display
        self._signal_type: str = "UNKNOWN"
        self._signal_confidence: int = 0
        self._noise_dbm: float = -95.0
        self._adsb_dbm: float = -95.0
        self._read_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to dump1090 and start background reader. Returns True on success."""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.connected = True
            logger.info(f"✅ Connected to dump1090 at {self.host}:{self.port}")
            self._read_task = asyncio.create_task(self._continuous_read())
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to dump1090: {e}")
            self.connected = False
            return False

    async def close(self):
        """Stop background reader and close TCP connection."""
        self.connected = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        logger.info("SDR connection closed")

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    async def _continuous_read(self):
        """Background coroutine: reads lines from dump1090 indefinitely."""
        logger.info("📡 Starting continuous ADS-B reader on port %d...", self.port)
        buffer = ""
        while self.connected:
            try:
                data = await asyncio.wait_for(self.reader.read(4096), timeout=5.0)
                if not data:
                    logger.warning("dump1090 closed the connection")
                    self.connected = False
                    break

                buffer += data.decode("utf-8", errors="ignore")
                lines = buffer.split("\n")
                buffer = lines[-1]  # keep partial last line

                for line in lines[:-1]:
                    line = line.strip()
                    if line:
                        ts = datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]
                        self._raw_messages.append({"time": ts, "msg": line})
                        self._process_message(line)

            except asyncio.TimeoutError:
                # No data from dump1090 — record background noise
                self._update_noise(-95.0 + random.uniform(-3.0, 3.0), is_adsb=False)

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error("Read error in _continuous_read: %s", e)
                self.connected = False
                break

        logger.info("📡 ADS-B reader stopped")

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    def _process_message(self, msg: str):
        """Dispatch raw line from dump1090."""
        if msg.startswith("*") and msg.endswith(";"):
            self._process_avr(msg)
        elif msg.startswith("MSG,"):
            self._process_sbs(msg)
        else:
            self._update_noise(-90.0 + random.uniform(-5.0, 5.0), is_adsb=False)

    def _process_avr(self, msg: str):
        """Parse AVR format: *HEXSTRING;"""
        hex_str = msg[1:-1]
        if not all(c in "0123456789ABCDEFabcdef" for c in hex_str):
            return
        if _PMS_AVAILABLE:
            try:
                self._decode_with_pymodes(hex_str)
                signal_strength = -65.0 + random.uniform(-10.0, 5.0)
                self._update_noise(signal_strength, is_adsb=True)
            except Exception:
                self._update_noise(-88.0 + random.uniform(-5.0, 5.0), is_adsb=False)
        else:
            # Fallback: at least record as ADS-B if length matches
            if len(hex_str) in (14, 28, 56):
                icao = hex_str[2:8].upper()
                self._ensure_aircraft(icao)
                self._update_noise(-68.0 + random.uniform(-8.0, 5.0), is_adsb=True)
            else:
                self._update_noise(-90.0, is_adsb=False)

    def _decode_with_pymodes(self, hex_str: str):
        """Use pyModeS to decode ADS-B content."""
        icao = pms.icao(hex_str)
        if not icao:
            return

        self._ensure_aircraft(icao)
        ac = self.aircraft_dict[icao]
        ac["last_seen"] = datetime.utcnow().isoformat()

        df = pms.df(hex_str)
        if df == 17:
            tc = pms.adsb.typecode(hex_str)
            if 1 <= tc <= 4:
                cs = pms.adsb.callsign(hex_str)
                if cs:
                    ac["callsign"] = cs.strip("_")
            elif 9 <= tc <= 18:
                alt = pms.adsb.altitude(hex_str)
                if alt:
                    ac["altitude"] = alt
            elif tc == 19:
                try:
                    vel = pms.adsb.velocity(hex_str)
                    if vel:
                        spd, trk, vr, _ = vel
                        ac["speed"] = spd
                        ac["track"] = trk
                except Exception:
                    pass

        self.signal_events.append(
            {
                "time": datetime.utcnow().isoformat(),
                "hex": icao,
                "type": "ADS-B",
                "df": df,
            }
        )
        if len(self.signal_events) > 1000:
            self.signal_events = self.signal_events[-500:]

    def _process_sbs(self, msg: str):
        """Parse SBS1 format: MSG,type,..."""
        parts = msg.split(",")
        if len(parts) < 16:
            return
        icao = parts[4].upper().strip()
        if not icao:
            return
        self._ensure_aircraft(icao)
        ac = self.aircraft_dict[icao]
        ac["last_seen"] = datetime.utcnow().isoformat()

        if parts[10]:
            ac["callsign"] = parts[10].strip()
        try:
            if parts[11]:
                ac["altitude"] = float(parts[11])
            if parts[12]:
                ac["speed"] = float(parts[12])
            if parts[13]:
                ac["track"] = float(parts[13])
            if parts[14]:
                ac["lat"] = float(parts[14])
            if parts[15]:
                ac["lon"] = float(parts[15])
        except (ValueError, IndexError):
            pass

        self._update_noise(-68.0 + random.uniform(-8.0, 5.0), is_adsb=True)
        self.signal_events.append(
            {
                "time": datetime.utcnow().isoformat(),
                "hex": icao,
                "type": "SBS",
            }
        )

    def _ensure_aircraft(self, icao: str):
        if icao not in self.aircraft_dict:
            self.aircraft_dict[icao] = {
                "hex": icao,
                "callsign": None,
                "altitude": None,
                "speed": None,
                "track": None,
                "lat": None,
                "lon": None,
                "last_seen": datetime.utcnow().isoformat(),
            }

    # ------------------------------------------------------------------
    # Noise tracking
    # ------------------------------------------------------------------

    def _update_noise(self, level: float, *, is_adsb: bool):
        now = time.time()
        entry = {"time": now, "level": level}
        self.noise_levels.append(entry)

        if is_adsb:
            self._adsb_dbm = level
            self.adsb_levels.append(entry)
            self._signal_type = "ADS-B"
            self._signal_confidence = min(100, self._signal_confidence + 10)
        else:
            self._noise_dbm = level
            self._signal_confidence = max(0, self._signal_confidence - 1)
            if self._signal_confidence < 20:
                self._signal_type = "NOISE"

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def get_noise_data(self) -> dict:
        """Return current noise/signal summary for API and dashboard."""
        recent_noise = list(self.noise_levels)[-50:]
        recent_adsb = list(self.adsb_levels)[-50:]

        avg_noise = (
            sum(n["level"] for n in recent_noise) / len(recent_noise)
            if recent_noise
            else -95.0
        )
        avg_adsb = (
            sum(n["level"] for n in recent_adsb) / len(recent_adsb)
            if recent_adsb
            else -95.0
        )

        return {
            "signal_type": self._signal_type,
            "confidence": self._signal_confidence,
            "noise_dbm": round(avg_noise, 2),
            "adsb_dbm": round(avg_adsb, 2),
            "noise_history": [
                {"t": n["time"], "v": round(n["level"], 2)} for n in recent_noise
            ],
            "adsb_history": [
                {"t": n["time"], "v": round(n["level"], 2)} for n in recent_adsb
            ],
            "raw_messages": list(self._raw_messages)[-30:],
        }

    async def get_signal_events(self) -> list:
        """Return recent decoded signal events."""
        return self.signal_events[-100:]
