#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "== Compile check =="
python -m compileall -q app ui tests

echo "== Functional tests =="
python -m pytest -q -m "not performance"

echo "== Performance tests =="
python -m pytest -q -m performance -s

echo "== Required artifacts =="
required=(
  README.md requirements.txt Dockerfile docker-compose.yml
  app/main.py app/api/assistant.py app/api/reasoning.py
  app/services/assistant.py app/services/reasoning.py
  data/sample_events.json scripts/generate_events.py tests
)
for path in "${required[@]}"; do
  test -e "$path" || { echo "Missing required artifact: $path"; exit 1; }
done

echo "VERIFY_ALL: PASS"
