#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ] || [ ! -d frontend/dist ]; then
  echo "请先运行：bash scripts/setup.sh"
  exit 1
fi
APP_PORT="${PARROT_PORT:-${SOUNDSCRIBE_PORT:-8765}}"
echo "Parrot：http://127.0.0.1:$APP_PORT"
echo "首次系统录音请允许当前终端的「屏幕与系统音频录制」权限。按 Ctrl+C 退出。"
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "$APP_PORT"
