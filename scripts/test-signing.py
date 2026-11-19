"""Verify code identity survives a resource update without changing the real app."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from client_signing import ROOT, resolve_identity


def signature(path, *flags):
    result = subprocess.run(["codesign", "--display", *flags, str(path)], capture_output=True, text=True, check=True)
    return result.stdout + result.stderr


def requirement(path):
    lines = signature(path, "-r-").splitlines()
    return next(line.removeprefix("designated => ") for line in lines if line.startswith("designated => "))


def main():
    original = ROOT / "dist/Parrot.app"
    identity = resolve_identity()
    identity.unlock()
    assert identity.identity != "-", "固定身份验证不接受临时签名。"
    old_requirement = requirement(original)
    helper_requirement = requirement(original / "Contents/MacOS/ParrotCapture")
    assert "cdhash" not in old_requirement
    assert '"com.alverlin.parrot"' in old_requirement
    assert '"com.alverlin.parrot.capture"' in helper_requirement
    old_hash = next(line for line in signature(original, "--verbose=4").splitlines() if line.startswith("CDHash="))
    with tempfile.TemporaryDirectory(prefix="parrot-signing-test-") as work:
        updated = Path(work) / "Parrot.app"
        shutil.copytree(original, updated)
        page = updated / "Contents/Resources/runtime/frontend/dist/index.html"
        page.write_bytes(page.read_bytes() + b"\n<!-- signing continuity test -->\n")
        identity.sign(updated, "com.alverlin.parrot")
        assert old_requirement == requirement(updated)
        assert helper_requirement == requirement(updated / "Contents/MacOS/ParrotCapture")
        new_hash = next(line for line in signature(updated, "--verbose=4").splitlines() if line.startswith("CDHash="))
        assert old_hash != new_hash
        for app in [original, updated]:
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
            subprocess.run(["codesign", "--verify", "--strict", "-R", "=" + old_requirement, str(app)], check=True)
            subprocess.run(["codesign", "--verify", "--strict", "-R", "=" + helper_requirement,
                            str(app / "Contents/MacOS/ParrotCapture")], check=True)
    print("PASS: changed app resources retain the same certificate-based designated requirement; nested signatures and both bundles verify.")
    print("此测试验证签名身份连续性，不代表用户已经授予录屏权限。")


if __name__ == "__main__":
    main()
