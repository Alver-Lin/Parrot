#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != "Darwin" ]; then
  echo "Parrot 的音频采集仅支持 macOS 13+，当前平台跳过编译。"
  exit 0
fi
PROJECT_PATH="$(pwd -P)"
CACHE_KEY="$(printf '%s' "$PROJECT_PATH" | shasum -a 256 | cut -c1-12)"
MODULE_CACHE="native/.build/$CACHE_KEY"
mkdir -p native/bin "$MODULE_CACHE"
swiftc -parse-as-library -O -target "$(uname -m)-apple-macosx13.0" -module-cache-path "$MODULE_CACHE" \
  -framework ScreenCaptureKit -framework AVFoundation -framework CoreMedia \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker native/CaptureInfo.plist \
  native/SystemAudioCapture.swift -o native/bin/ParrotCapture
echo "音频采集组件已编译。"
