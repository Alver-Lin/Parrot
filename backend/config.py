import os
import platform
from pathlib import Path

from .legacy_data import import_legacy_into, remember_legacy_path, support_dir

ROOT = Path(__file__).resolve().parent.parent
# Dev / source builds default to project .data. The packaged app sets PARROT_DATA
# to ~/Library/Application Support/Parrot via the desktop host.
DATA = Path(os.environ.get("PARROT_DATA", os.environ.get("SOUNDSCRIBE_DATA", str(ROOT / ".data"))))
DATA.mkdir(parents=True, exist_ok=True)
try:
    # Keep install and dev stores separate. Only the install store may import once
    # from a legacy .data path when it still has no sessions.
    if DATA.resolve() == support_dir().resolve():
        import_legacy_into(DATA)
    else:
        remember_legacy_path(DATA)
except OSError:
    pass
os.environ.setdefault("HF_HOME", str(DATA / "models"))
APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"
CAPTURE_BINARY = Path(os.environ.get("PARROT_CAPTURE_BINARY", str(ROOT / "native" / "bin" / "ParrotCapture")))
DESKTOP_TOKEN = os.environ.get("PARROT_DESKTOP_TOKEN", "")
SAMPLE_RATE = 16000
# After recording stops, keep the ASR model warm briefly, then free memory.
MODEL_IDLE_RELEASE_SECONDS = 300
