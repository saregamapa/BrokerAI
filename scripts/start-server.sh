#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -d .venv ]; then
  echo "Create venv first: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi
# shellcheck source=/dev/null
source .venv/bin/activate
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
exec uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
