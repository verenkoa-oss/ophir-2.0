"""
OPHIR SDR Real Module
Reads ADS-B data from dump1090 via TCP RAW port 30001
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DUMP1090_HOST = "localhost"
DUMP1090_PORT = 30001


class SDRReader:
    """Read ADS-B RAW messages from dump1090 port 30001"""

    def __init__(self):
        self.host = DUMP1090_HOST
        self.port = DUMP1090_PORT
        self.reader = None
        self.writer = None
        self.connected = False
        self.aircraft_dict = {}
        self.signal_events = []
        self.noise_dbm = 0.0
        self.message_count = 0
        self._reader_task = None
        self._connect_and_start()

    def _connect_and_start(self):
        """Schedule async connection and reader task startup."""
        loop = asyncio.get_event_loop()
        loop.create_task(self._init())

    async def _init(self):
        """Initialise connection and start the continuous reader."""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.connected = True
            logger.info(
                f"✅ Connected to dump1090 RAW mode at {self.host}:{self.port}"
            )
            self._reader_task = asyncio.create_task(self._continuous_read())
            logger.info("✅ Reader task STARTED!")
        except Exception as e:
            logger.error(f"❌ Failed to connect to dump1090: {e}")
            self.connected = False

    async def _continuous_read(self):
        """Continuously read lines from dump1090 RAW port.

        dump1090 sends messages in the format::

            *8D406B902015A678D4D220AA4BAA;<newline>

        Each message ends with ';' followed by a newline.  We use
        ``readline()`` so that we read one complete line per call,
        which correctly handles the dump1090 RAW protocol.
        """
        logger.info("🔴 _continuous_read() STARTED!")
        while self.connected:
            try:
                raw = await asyncio.wait_for(
                    self.reader.readline(),
                    timeout=2.0,
                )
                if not raw:
                    logger.warning("⚠️ Connection closed by dump1090")
                    self.connected = False
                    break

                line = raw.decode("utf-8", errors="ignore").strip()

                # Skip empty lines
                if not line:
                    continue

                self.message_count += 1
                logger.info(f"📡 MSG: {line}")

                await self._process_message(line)

            except asyncio.TimeoutError:
                # Normal – no traffic right now; keep looping
                continue
            except Exception as e:
                logger.error(f"❌ Read error: {e}")
                await asyncio.sleep(1.0)

    async def _process_message(self, line: str):
        """Process a single RAW ADS-B message line.

        Expected format: *<hex_payload>;
        """
        try:
            # Strip leading '*' and trailing ';'
            if line.startswith("*") and line.endswith(";"):
                hex_data = line[1:-1]
            else:
                hex_data = line.lstrip("*").rstrip(";")

            if len(hex_data) < 14:
                return

            # Decode ICAO address from bytes 1-3 of the message
            icao = hex_data[2:8].upper()

            # Simulate noise / RSSI from message length as a rough proxy
            noise = -20.0 - (len(hex_data) % 30)
            self.noise_dbm = noise

            now = datetime.now(timezone.utc)

            # Update or create aircraft entry
            if icao not in self.aircraft_dict:
                self.aircraft_dict[icao] = {
                    "hex": icao,
                    "first_seen": now.isoformat(),
                    "last_seen": now.isoformat(),
                    "messages": 0,
                    "noise_dbm": noise,
                }
            self.aircraft_dict[icao]["last_seen"] = now.isoformat()
            self.aircraft_dict[icao]["messages"] += 1
            self.aircraft_dict[icao]["noise_dbm"] = noise

            # Record signal event
            event = {
                "timestamp": now.isoformat(),
                "icao": icao,
                "raw": line,
                "noise_dbm": noise,
                "signal_type": "ADS-B",
            }
            self.signal_events.append(event)

            # Keep event history bounded
            if len(self.signal_events) > 1000:
                self.signal_events = self.signal_events[-500:]

        except Exception as e:
            logger.debug(f"Failed to process message '{line}': {e}")

    async def get_noise_data(self):
        """Return current noise / signal metrics."""
        return {
            "signal_type": "ADS-B" if self.message_count > 0 else "UNKNOWN",
            "confidence": min(100, self.message_count),
            "noise_dbm": self.noise_dbm,
        }

    async def get_signal_events(self):
        """Return recorded signal events."""
        return list(self.signal_events)

    async def close(self):
        """Close the connection and cancel the reader task."""
        self.connected = False
        if self._reader_task:
            self._reader_task.cancel()
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        logger.info("SDR connection closed")
