#!/bin/bash

echo "🚀 OPHIR 2.0 - AUTONOMOUS SYSTEM LAUNCHER"
echo "=========================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found!"
    exit 1
fi

# Check requirements.txt
if [ ! -f "requirements.txt" ]; then
    echo "❌ requirements.txt not found!"
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
if ! pip3 install -r requirements.txt; then
    echo "❌ Failed to install dependencies!"
    exit 1
fi

# Check dump1090 (basic/mutability only — dump1090-fa is not supported)
if ! command -v dump1090 &> /dev/null; then
    echo "⚠️  dump1090 not found - install with: sudo apt-get install dump1090-mutability"
fi

# Check Ollama
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️  Ollama not running - start with: ollama serve"
fi

# Run system
echo ""
echo "🚀 Starting OPHIR 2.0..."
python3 run.py
