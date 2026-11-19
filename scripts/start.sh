#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != "Darwin" ]; then
  echo "Parrot 客户端目前仅支持 macOS 13+。"
  exit 1
fi
if [ ! -d dist/Parrot.app ]; then
  echo "请先运行：bash scripts/setup.sh"
  exit 1
fi
echo "正在启动 Parrot，点击屏幕左侧的悬浮球展开转写面板。"
exec /usr/bin/open "$PWD/dist/Parrot.app"
