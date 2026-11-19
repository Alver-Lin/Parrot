#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "${PARROT_PORT:-8765}"
