"""Build a macOS app using the local Python environment and shared Parrot data."""

import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from client_signing import resolve_identity

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def build(app, identity, python):
    macos = app / "Contents/MacOS"
    resources = app / "Contents/Resources"
    runtime = resources / "runtime"
    macos.mkdir(parents=True)
    runtime.mkdir(parents=True)
    cache_key = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    cache = ROOT / "native/.build" / cache_key
    cache.mkdir(parents=True, exist_ok=True)
    run(
        "swiftc",
        "-parse-as-library",
        "-swift-version",
        "5",
        "-O",
        "-target",
        platform.machine() + "-apple-macosx13.0",
        "-module-cache-path",
        cache,
        "-framework",
        "AppKit",
        "-framework",
        "AVFoundation",
        "-framework",
        "WebKit",
        "-framework",
        "UniformTypeIdentifiers",
        "-framework",
        "ApplicationServices",
        "-framework",
        "Security",
        "-framework",
        "LocalAuthentication",
        # Resolve CGS/SkyLight symbols at runtime. Linking PrivateFrameworks via -F
        # makes WebKit pick a private Network.framework and break the current CLT SDK.
        "-Xlinker",
        "-undefined",
        "-Xlinker",
        "dynamic_lookup",
        ROOT / "native/ParrotDesktop.swift",
        ROOT / "native/PolishSettings.swift",
        "-o",
        macos / "Parrot",
    )
    run("bash", "scripts/build-capture.sh")
    shutil.copy2(ROOT / "native/bin/ParrotCapture", macos / "ParrotCapture")
    shutil.copytree(
        ROOT / "backend", runtime / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    shutil.copytree(ROOT / "frontend/dist", runtime / "frontend/dist")
    info = {
        "CFBundleName": "Parrot",
        "CFBundleDisplayName": "Parrot",
        "CFBundleIdentifier": "com.alverlin.parrot",
        "CFBundleExecutable": "Parrot",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0.3",
        "CFBundleVersion": "3",
        "CFBundleIconFile": "Parrot.icns",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "LSMultipleInstancesProhibited": True,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "ParrotPythonExecutable": str(python),
        "ParrotDataDirectory": str(ROOT / ".data"),
        "NSScreenCaptureUsageDescription": "采集电脑播放的课程声音并转成文字，不保存屏幕画面。",
        "NSAudioCaptureUsageDescription": "将本机播放的声音转成文字，不保存完整录音。",
        "NSMicrophoneUsageDescription": "选择麦克风或双声源模式时，将麦克风声音转成文字，不保存完整录音。",
    }
    with (app / "Contents/Info.plist").open("wb") as output_file:
        plistlib.dump(info, output_file)
    shutil.copy2(ROOT / "native/AppIcon.png", resources / "AppIcon.png")
    iconset = app.parent / "Parrot.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    run(macos / "Parrot", "--write-iconset", iconset)
    run("iconutil", "-c", "icns", iconset, "-o", resources / "Parrot.icns")
    # Sign nested code first with its own identifier, then seal the app.
    identity.sign(macos / "ParrotCapture", "com.alverlin.parrot.capture")
    identity.sign(app, "com.alverlin.parrot")
    run("codesign", "--verify", "--deep", "--strict", app)


def main():
    if platform.system() != "Darwin":
        raise SystemExit("当前客户端仅支持 macOS 13+。")
    python = Path(os.environ.get("PARROT_PYTHON", str(ROOT / ".venv/bin/python")))
    if not python.is_file():
        raise SystemExit("未找到 Python 环境，请先运行 bash scripts/setup.sh。")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("请先在 frontend 目录执行 npm run build。")
    # Fail before touching the working app if signing was not configured.
    identity = resolve_identity()
    identity.unlock()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    destination = output / "Parrot.app"
    with tempfile.TemporaryDirectory(prefix=".parrot-build-", dir=output) as work:
        work = Path(work)
        build(work / "Parrot.app", identity, python)
        previous = work / "previous.app"
        if destination.exists():
            destination.rename(previous)
        try:
            (work / "Parrot.app").rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    print(f"客户端已构建：{destination}")
    print("签名身份：" + identity.identity)
    print("当前为本机客户端，使用此项目的 Python 环境与 .data 数据目录（与安装版隔离）。")


if __name__ == "__main__":
    main()
