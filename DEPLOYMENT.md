# OPHIR 2.0 Deployment Guide

> **Новичок?** Смотри подробную пошаговую инструкцию: [INSTALL_GUIDE.md](INSTALL_GUIDE.md)

## System Requirements

- Ubuntu 20.04+ / Debian 11+ (or macOS / Windows)
- Python 3.8+ (3.12+ recommended)
- 2GB+ RAM (4GB+ recommended for Ollama/Mistral)
- RTL-SDR USB dongle (optional — system runs in demo mode without it)

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/verenkoa-oss/ophir-2.0.git ophir-2.0
cd ophir-2.0
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate.bat  # Windows CMD
```

### 3. Install Python Dependencies

```bash
pip3 install -r requirements.txt
```

### 4. Install External Services

#### Ollama (AI/LLM — recommended)
```bash
# Linux:
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull mistral:latest
```

#### dump1090 (RTL-SDR — optional)
```bash
# Ubuntu/Debian:
sudo apt install dump1090-fa -y
```

### 5. Start the System

```bash
# Start Ollama (in a separate terminal):
ollama serve

# Start dump1090 if you have RTL-SDR hardware (in a separate terminal):
dump1090-fa --raw --net --quiet

# Start OPHIR:
python3 run.py
# or
bash start.sh
```

### 6. Access the Dashboard

- Local: http://localhost:8080
- Network: http://YOUR_IP:8080
- API Docs: http://localhost:8080/docs

### 7. Stop

Press `Ctrl+C` in the terminal running `python3 run.py`.

## Configuration

All settings are in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `PORT` | `8080` | HTTP server port |
| `OLLAMA_MODEL` | `mistral:latest` | LLM model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `DUMP1090_HOST` | `localhost` | dump1090 host |
| `DUMP1090_PORT` | `30001` | dump1090 TCP port |
| `OBSERVER_LATITUDE` | `31.073541` | Observer GPS latitude |
| `OBSERVER_LONGITUDE` | `35.037383` | Observer GPS longitude |
| `ENABLE_LLM_ANALYSIS` | `True` | Enable/disable LLM |

## Clean Uninstall

```bash
# Stop running processes
pkill -f "python3 run.py" 2>/dev/null || true
pkill -f uvicorn 2>/dev/null || true

# Remove directory
cd ~
rm -rf ophir-2.0
```
