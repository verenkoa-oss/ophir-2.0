# OPHIR 2.0 - Signal Intelligence System

## Quick Start

### Prerequisites
- Python 3.8+
- dump1090 or dump1090-fa
- Ollama (for LLM analysis)
- WiFi network

### Installation

```bash
# Clone repository
git clone https://github.com/verenkoa-oss/ophir-2.0.git
cd ophir-2.0

# Install dependencies
pip3 install -r requirements.txt
```

### Running the System

```bash
# Method 1: Python
python3 run.py

# Method 2: Bash
bash start.sh

# Method 3: One-liner
python3 -c "from run import OphirSystem; OphirSystem().run()"
```

### Access Dashboard

- Local: http://localhost:8080
- Network: http://YOUR_IP:8080
- API Docs: http://localhost:8080/docs

### Observer Location
- Latitude: 31.073541°N
- Longitude: 35.037383°E

### Graceful Shutdown
Press Ctrl+C in terminal for graceful shutdown.

## Features

✅ Real-time ADS-B signal parsing
✅ LLM-powered aircraft classification
✅ Distance calculation from RSSI
✅ Unknown aircraft detection
✅ Live dashboard with oscilloscope
✅ Gain and noise controls
✅ System On/Off button
✅ 24/7 autonomous operation
✅ Cross-device WiFi access
✅ Signal records tracking

## Architecture

- Backend: FastAPI + WebSocket
- Frontend: Vanilla JS + Leaflet + Chart.js
- LLM: Ollama (mistral:latest)
- SDR: dump1090-fa (BASIC mode)
- Database: SQLite

## Network

- API Base: http://0.0.0.0:8080/api/v1
- WebSocket: ws://0.0.0.0:8080/ws/live
- Dashboard: http://0.0.0.0:8080/

## GitHub Actions / CI

This project uses GitHub Actions via Copilot agent sessions. The free GitHub Free plan includes **2,000 Actions minutes/month**. If you see limit errors:

- Check your Actions usage: Settings → Billing → Usage this month
- See [GITHUB_STATUS.md](./GITHUB_STATUS.md) for a full analysis of subscription status and available options

## Support

For issues or questions, create GitHub issue.
