"""Persistent code identities for local builds; never silently use ad-hoc signing."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIGNING = ROOT / ".data/signing"
CONFIG = SIGNING / "identity.json"


@dataclass
class SigningIdentity:
    identity: str
    keychain: str = ""
    password_file: str = ""

    def unlock(self):
        if self.password_file:
            result = subprocess.run(
                ["security", "unlock-keychain", "-p", Path(self.password_file).read_text().strip(), self.keychain],
                capture_output=True, text=True,
            )
            if result.returncode:
                raise SystemExit("无法解锁 Parrot 本地签名钥匙串；请修复原证书，不要重新生成签名身份。")

    def sign(self, path, identifier):
        command = ["codesign", "--force", "--timestamp=none", "--sign", self.identity,
                   "--identifier", identifier]
        if self.keychain:
            command += ["--keychain", self.keychain]
        subprocess.run(command + [str(path)], check=True)


def resolve_identity():
    explicit = os.environ.get("PARROT_SIGNING_IDENTITY", "").strip()
    if explicit == "-" or (not explicit and os.environ.get("PARROT_ALLOW_ADHOC") == "1"):
        if os.environ.get("PARROT_ALLOW_ADHOC") != "1":
            raise SystemExit("临时签名会使权限失效；仅测试时设置 PARROT_ALLOW_ADHOC=1。")
        print("警告：显式启用了 ad-hoc 测试构建，屏幕录制授权可能在更新后失效。")
        return SigningIdentity("-")
    if explicit:
        return SigningIdentity(explicit, os.environ.get("PARROT_SIGNING_KEYCHAIN", ""))
    if not CONFIG.exists():
        raise SystemExit("尚未配置固定签名身份。请先运行 .venv/bin/python scripts/setup-signing.py。")
    config = json.loads(CONFIG.read_text())
    if not re.fullmatch(r"[A-Fa-f0-9]{40}", config.get("identity", "")):
        raise SystemExit("签名配置损坏，请恢复 .data/signing；不会自动更换证书或使用临时签名。")
    identity = SigningIdentity(config["identity"], config["keychain"], config["password_file"])
    if not Path(identity.keychain).is_file() or not Path(identity.password_file).is_file():
        raise SystemExit("固定签名钥匙串缺失，请恢复 .data/signing；重新生成证书将需要再次授权。")
    identity.unlock()
    return identity
