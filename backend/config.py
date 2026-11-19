import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("PARROT_DATA", os.environ.get("SOUNDSCRIBE_DATA", str(ROOT / ".data"))))
DATA.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(DATA / "models"))
APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"
CAPTURE_BINARY = Path(os.environ.get("PARROT_CAPTURE_BINARY", str(ROOT / "native" / "bin" / "ParrotCapture")))
DESKTOP_TOKEN = os.environ.get("PARROT_DESKTOP_TOKEN", "")
SAMPLE_RATE = 16000
