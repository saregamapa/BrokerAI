#!/usr/bin/env bash
# BrokerAI — one-shot setup + launch
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "📁 Working in: $ROOT"

# Create venv if missing
if [ ! -d .venv ]; then
  echo "🔧 Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install dependencies
echo "📦 Installing dependencies (first run may take a minute)..."
pip install -r requirements.txt -q

# Kill any existing uvicorn on port 8000
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
sleep 1

echo ""
echo "🚀 Starting BrokerAI at http://127.0.0.1:8000"
echo "   Press Ctrl+C to stop."
echo ""

exec uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
