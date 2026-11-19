"""Exercise real native windows, WebKit and the private backend with isolated data."""

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    app = ROOT / "dist/Parrot.app"
    binary = app / "Contents/MacOS/Parrot"
    runtime = app / "Contents/Resources/runtime"
    python = os.environ.get("PARROT_PYTHON", str(ROOT / ".venv/bin/python"))
    artifacts = ROOT / ".data/client-test-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parrot-native-test-") as data:
        env = {
            **os.environ,
            "PARROT_DATA": data,
            "PYTHONPATH": str(runtime),
            "PARROT_PYTHON": python,
            "PARROT_SELF_TEST_OUTPUT": str(artifacts),
        }
        seed = """
from backend.schemas import Settings
from backend.store import Store
store = Store()
sid = store.create(Settings(title='客户端功能验证'))
store.update(sid, status='completed', duration=15, processed=15)
store.add_segments(sid, [{'start':0,'end':15,'text':'客户端功能验证。English words are preserved.','review':False}])
"""
        subprocess.run([python, "-c", seed], cwd=runtime, env=env, check=True)
        try:
            subprocess.run([str(binary), "--self-test"], cwd=ROOT, env=env, timeout=120, check=True)
        except Exception:
            log = Path(data) / "desktop.log"
            if log.exists():
                print(log.read_text()[-6000:])
            raise
    print(f"原生客户端验证完成，截图：{artifacts}")


if __name__ == "__main__":
    main()
