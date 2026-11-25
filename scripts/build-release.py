"""Build a redistributable macOS Parrot.app and versioned .pkg installer."""

from __future__ import annotations

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
VERSION = os.environ.get("PARROT_VERSION", "1.0.3").strip() or "1.0.3"
BUNDLE_VERSION = os.environ.get("PARROT_BUNDLE_VERSION", "4").strip() or "4"
IDENTIFIER = "com.alverlin.parrot"
PKG_ID = "com.alverlin.parrot.pkg"


def run(*args, cwd=ROOT, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True, env=env)


def machine_tag() -> str:
    return "arm64" if platform.machine() == "arm64" else "x86_64"


def ensure_frontend():
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("请先在 frontend 目录执行 npm run build。")


STANDALONE_PYTHON = {
    "arm64": (
        "cpython-3.12.16+20261110-aarch64-apple-darwin-install_only.tar.gz",
        "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/"
        "cpython-3.12.16%2B20261110-aarch64-apple-darwin-install_only.tar.gz",
    ),
    "x86_64": (
        "cpython-3.12.16+20261110-x86_64-apple-darwin-install_only.tar.gz",
        "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/"
        "cpython-3.12.16%2B20261110-x86_64-apple-darwin-install_only.tar.gz",
    ),
}


def download_standalone_python(arch: str) -> Path:
    name, url = STANDALONE_PYTHON[arch]
    cache = ROOT / "dist" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / name
    if not archive.is_file():
        print(f"下载独立 Python：{name}")
        run("curl", "-fL", "--retry", "3", "-o", archive, url)
    return archive


def create_bundled_python(target: Path, host_python: Path):
    del host_python  # Release embeds a standalone interpreter; local venv is CLT-linked.
    if target.exists():
        shutil.rmtree(target)
    arch = platform.machine()
    if arch not in STANDALONE_PYTHON:
        raise SystemExit(f"暂不支持架构 {arch} 的发行包。")
    archive = download_standalone_python(arch)
    with tempfile.TemporaryDirectory(prefix=".parrot-python-", dir=ROOT / "dist") as work:
        work = Path(work)
        run("tar", "-xzf", archive, "-C", work)
        extracted = work / "python"
        if not extracted.is_dir():
            raise SystemExit("独立 Python 压缩包格式异常。")
        shutil.move(str(extracted), target)
    python = target / "bin" / "python3"
    if not python.is_file():
        raise SystemExit("发行包 Python 环境创建失败。")
    run(python, "-m", "ensurepip", "--upgrade")
    run(python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
    run(python, "-m", "pip", "install", "-r", ROOT / "backend/requirements.txt")
    for path in target.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)
    return python


def compile_native(app: Path, identity, cache: Path):
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
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
    identity.sign(macos / "ParrotCapture", "com.alverlin.parrot.capture")


def write_info_plist(app: Path):
    info = {
        "CFBundleName": "Parrot",
        "CFBundleDisplayName": "Parrot",
        "CFBundleIdentifier": IDENTIFIER,
        "CFBundleExecutable": "Parrot",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": BUNDLE_VERSION,
        "CFBundleIconFile": "Parrot.icns",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "LSMultipleInstancesProhibited": True,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        # Relative to Contents/Resources — resolved by the native client.
        "ParrotPythonExecutable": "python/bin/python3",
        "NSScreenCaptureUsageDescription": "采集电脑播放的课程声音并转成文字，不保存屏幕画面。",
        "NSAudioCaptureUsageDescription": "将本机播放的声音转成文字，不保存完整录音。",
        "NSMicrophoneUsageDescription": "选择麦克风或双声源模式时，将麦克风声音转成文字，不保存完整录音。",
    }
    with (app / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)


def build_app(app: Path, identity, host_python: Path):
    resources = app / "Contents/Resources"
    runtime = resources / "runtime"
    resources.mkdir(parents=True)
    cache = ROOT / "native/.build" / hashlib.sha256(f"release-{VERSION}".encode()).hexdigest()[:12]
    cache.mkdir(parents=True, exist_ok=True)

    compile_native(app, identity, cache)
    shutil.copytree(
        ROOT / "backend",
        runtime / "backend",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    shutil.copytree(ROOT / "frontend/dist", runtime / "frontend/dist")
    create_bundled_python(resources / "python", host_python)
    write_info_plist(app)

    shutil.copy2(ROOT / "native/AppIcon.png", resources / "AppIcon.png")
    iconset = app.parent / "Parrot.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    run(app / "Contents/MacOS/Parrot", "--write-iconset", iconset)
    run("iconutil", "-c", "icns", iconset, "-o", resources / "Parrot.icns")
    shutil.rmtree(iconset, ignore_errors=True)

    identity.sign(app, IDENTIFIER)
    run("codesign", "--verify", "--deep", "--strict", app)


def write_component_plist(app: Path) -> Path:
    # Package only Parrot.app from the staging directory.
    plist = app.parent / "component.plist"
    payload = [
        {
            "BundleHasStrictIdentifier": True,
            "BundleIsRelocatable": False,
            "BundleIsVersionChecked": True,
            "BundleOverwriteAction": "upgrade",
            "RootRelativeBundlePath": app.name,
        }
    ]
    with plist.open("wb") as handle:
        plistlib.dump(payload, handle)
    return plist


def stage_app_only(app: Path, stage: Path) -> Path:
    staged = stage / "Parrot.app"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(app, staged, symlinks=True)
    return staged


def write_checksum(pkg: Path):
    digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
    (pkg.with_suffix(pkg.suffix + ".sha256")).write_text(f"{digest}  {pkg.name}\n")
    return digest


def main():
    if platform.system() != "Darwin":
        raise SystemExit("发行包仅支持在 macOS 上构建。")
    ensure_frontend()
    host_python = Path(os.environ.get("PARROT_PYTHON", str(ROOT / ".venv/bin/python")))
    if not host_python.is_file():
        raise SystemExit("未找到 Python 环境，请先运行 bash scripts/setup.sh。")

    identity = resolve_identity()
    identity.unlock()

    output = ROOT / "dist" / "release"
    output.mkdir(parents=True, exist_ok=True)
    app_name = f"Parrot-{VERSION}-{machine_tag()}.app"
    pkg_name = f"Parrot-{VERSION}-{machine_tag()}.pkg"
    app_out = output / app_name
    pkg_out = output / pkg_name

    with tempfile.TemporaryDirectory(prefix=".parrot-release-", dir=ROOT / "dist") as work:
        work = Path(work)
        built = work / "Parrot.app"
        build_app(built, identity, host_python)
        if app_out.exists():
            shutil.rmtree(app_out)
        shutil.copytree(built, app_out, symlinks=True)

        stage = work / "pkgroot"
        staged_app = stage_app_only(built, stage)
        # pkgbuild --root expects the app under install-location; use component plist.
        # Re-run pkgbuild with only the app in the root.
        if pkg_out.exists():
            pkg_out.unlink()
        scripts = ROOT / "scripts/pkg"
        for name in ("preinstall", "postinstall"):
            path = scripts / name
            path.chmod(path.stat().st_mode | 0o111)
        component = write_component_plist(staged_app)
        run(
            "pkgbuild",
            "--root",
            stage,
            "--component-plist",
            component,
            "--identifier",
            PKG_ID,
            "--version",
            VERSION,
            "--install-location",
            "/Applications",
            "--scripts",
            scripts,
            pkg_out,
        )

    digest = write_checksum(pkg_out)
    print(f"发行应用：{app_out}")
    print(f"安装包：{pkg_out}")
    print(f"SHA256：{digest}")
    print("说明：当前使用本机开发签名。公开分发若未公证，用户首次打开可能需在系统设置中允许。")


if __name__ == "__main__":
    main()
