#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null || { echo "请先安装 Python 3.9+（推荐 3.11/3.12）。"; exit 1; }
command -v npm >/dev/null || { echo "请先安装 Node.js 20.19+ 或 22.12+。"; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
bash scripts/build-capture.sh
cd frontend
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
npm run build
echo "安装完成。运行 bash scripts/start.sh，打开 http://127.0.0.1:8765"
