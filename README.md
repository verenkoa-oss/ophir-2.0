# OPHIR 2.0 - Signal Intelligence System

## Quick Start

### Prerequisites
- Python 3.8+
- dump1090
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
- SDR: dump1090 (BASIC mode)
- Database: SQLite

## Network

- API Base: http://0.0.0.0:8080/api/v1
- WebSocket: ws://0.0.0.0:8080/ws/live
- Dashboard: http://0.0.0.0:8080/

## GitHub Actions

GitHub Actions is **not required** for this project. The table below clarifies when it is needed:

| Situation | Needs Actions? |
|---|---|
| Solo developer, personal project | ❌ No |
| Building and running the project locally | ❌ No |
| SDR / DSP / radio project for personal use | ❌ No |
| Team of 5+ with automated CI/CD | ✅ Yes |

The `.github/workflows/` directory is intentionally empty — no Actions workflows are configured or running.  
This means **zero Actions minutes are consumed** and there is nothing to pay for.

**What you need as a solo developer:**
- `git push` to save your code on GitHub — free, unlimited
- Build and run the project on your own machine — no GitHub infrastructure needed
- GitHub Free plan covers unlimited public/private repositories and 2,000 Actions minutes/month (unused here)

**When Actions would matter:**
- Automating tests or builds on every push (CI/CD)
- Deploying automatically to a server
- Working in a team where multiple people push code

**Bottom line:** Write code, `git push`, build locally. No Actions setup or payment needed.

## Support

For issues or questions, create GitHub issue.

For information on checking your GitHub subscription status (Pro/Free), billing plan, payment history and plan limits, see [GITHUB_BILLING.md](GITHUB_BILLING.md).
