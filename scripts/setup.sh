#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != "Darwin" ]; then
  echo "Parrot 客户端目前仅支持 macOS 13+。"
  exit 1
fi
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
cd ..
.venv/bin/python scripts/setup-signing.py
.venv/bin/python scripts/build-client.py
echo "安装完成。双击 dist/Parrot.app，或运行 bash scripts/start.sh。"
