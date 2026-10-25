#!/usr/bin/env bash
# Start the Zenith API and dashboard together (Linux/macOS or Docker).
set -euo pipefail
cd "$(dirname "$0")"

API_PORT="${ZENITH_PORT:-8000}"
UI_PORT="${STREAMLIT_PORT:-8501}"

python -m uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" &
API_PID=$!

python -m streamlit run ui/dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "$UI_PORT" \
  --server.headless true &
UI_PID=$!

cleanup() {
  kill "$API_PID" "$UI_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Zenith API:       http://localhost:${API_PORT}"
echo "API docs:         http://localhost:${API_PORT}/docs"
echo "Dashboard:        http://localhost:${UI_PORT}"

# Exit when either process stops; the trap cleans up the other one.
wait -n "$API_PID" "$UI_PID"
