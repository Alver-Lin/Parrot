#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != "Darwin" ]; then
  echo "系统声音采集组件仅需在 macOS 上编译。其他平台支持导入音频。"
  exit 0
fi
mkdir -p native/bin native/.build
swiftc -parse-as-library -O -target "$(uname -m)-apple-macosx13.0" -module-cache-path native/.build \
  -framework ScreenCaptureKit -framework AVFoundation -framework CoreMedia \
  native/SystemAudioCapture.swift -o native/bin/ParrotCapture
echo "系统声音采集组件已编译。"
