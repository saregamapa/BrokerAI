#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
kill "$(cat _uvicorn.pid 2>/dev/null)" 2>/dev/null || true
sleep 1
if command -v lsof >/dev/null 2>&1; then
  lsof -ti:8000 | xargs kill -9 2>/dev/null || true
fi
sleep 1
# shellcheck source=/dev/null
source .venv/bin/activate
nohup uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload > _uvicorn.log 2>&1 &
echo $! > _uvicorn.pid
sleep 4
curl -sS "http://127.0.0.1:8000/health" | tee _health_after_restart.json
echo ""
echo "PID: $(cat _uvicorn.pid)"
echo "Log: $ROOT/_uvicorn.log"
