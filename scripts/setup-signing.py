"""Create a persistent, non-exportable signing key for this Mac's Parrot builds.

No global trust roots are installed. Public distribution still needs Developer ID.
"""

import hashlib
import json
import os
import platform
import secrets
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from client_signing import CONFIG, SIGNING, resolve_identity


def execute(*args, env=None):
    result = subprocess.run([str(arg) for arg in args], env=env, capture_output=True, text=True)
    if result.returncode:
        # security arguments may contain passwords; never include the command.
        raise SystemExit(f"{args[0]} 操作失败：{result.stderr.strip() or '请检查钥匙串访问权限'}")
    return result.stdout


def finish_setup(work, keychain, password_file):
    certificate = work / "certificate.pem"
    certificate.write_text(execute("security", "find-certificate", "-c", "Parrot Local Development", "-p", keychain))
    execute("/usr/bin/openssl", "x509", "-in", certificate, "-outform", "DER", "-out", work / "certificate.der")
    fingerprint = hashlib.sha1((work / "certificate.der").read_bytes()).hexdigest().upper()
    probe = work / "signing-probe"
    # Do not copy SIP-protected flags from Apple's system executable.
    shutil.copyfile("/usr/bin/true", probe)
    probe.chmod(0o700)
    execute("codesign", "--force", "--timestamp=none", "--keychain", keychain,
            "--sign", fingerprint, "--identifier", "com.alverlin.parrot.signing-probe", probe)
    execute("codesign", "--verify", "--strict", probe)
    shutil.copyfile(certificate, SIGNING / "certificate.pem")
    CONFIG.write_text(json.dumps({"identity": fingerprint, "keychain": str(keychain),
                                  "password_file": str(password_file)}, indent=2) + "\n")
    CONFIG.chmod(0o600)


def main():
    if platform.system() != "Darwin":
        raise SystemExit("本地签名设置仅支持 macOS。")
    if CONFIG.exists():
        identity = resolve_identity()
        print(f"继续使用原签名身份：{identity.identity}")
        return
    SIGNING.mkdir(parents=True, exist_ok=True, mode=0o700)
    SIGNING.chmod(0o700)
    keychain = SIGNING / "ParrotDevelopment.keychain-db"
    password_file = SIGNING / "keychain-password"
    # Resume an interrupted setup using the SAME imported key and certificate.
    # Never replace them with a newly generated identity.
    if keychain.exists() and password_file.exists():
        execute("security", "unlock-keychain", "-p", password_file.read_text().strip(), keychain)
        with tempfile.TemporaryDirectory(prefix="certificate-", dir=SIGNING) as work:
            finish_setup(Path(work), keychain, password_file)
        print("已使用原钥匙串完成签名配置，未更换证书。")
        return
    if any(SIGNING.iterdir()):
        raise SystemExit("签名目录已存在但不完整。请恢复原身份；不会自动覆盖或重建证书。")
    password = secrets.token_hex(32)
    password_file.write_text(password)
    password_file.chmod(0o600)
    try:
        with tempfile.TemporaryDirectory(prefix="certificate-", dir=SIGNING) as work:
            work = Path(work)
            config = work / "openssl.cnf"
            config.write_text("""[req]
distinguished_name = subject
x509_extensions = extensions
prompt = no
[subject]
CN = Parrot Local Development
O = Parrot Local Development
[extensions]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
subjectKeyIdentifier = hash
""")
            env = {**os.environ, "PARROT_CERT_PASSWORD": password}
            execute("/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                    "-days", "3650", "-config", config, "-keyout", work / "key.pem",
                    "-out", work / "certificate.pem", "-passout", "env:PARROT_CERT_PASSWORD", env=env)
            execute("/usr/bin/openssl", "pkcs12", "-export", "-inkey", work / "key.pem",
                    "-in", work / "certificate.pem", "-out", work / "identity.p12",
                    "-passin", "env:PARROT_CERT_PASSWORD", "-passout", "env:PARROT_CERT_PASSWORD", env=env)
            execute("security", "create-keychain", "-p", password, keychain)
            execute("security", "set-keychain-settings", "-lut", "21600", keychain)
            execute("security", "unlock-keychain", "-p", password, keychain)
            execute("security", "import", work / "identity.p12", "-k", keychain,
                    "-P", password, "-x", "-T", "/usr/bin/codesign")
            execute("security", "set-key-partition-list", "-S", "apple-tool:,apple:",
                    "-s", "-k", password, keychain)
            finish_setup(work, keychain, password_file)
    finally:
        current = shlex.split(execute("security", "list-keychains", "-d", "user"))
        remaining = [path for path in current if Path(path).resolve() != keychain.resolve()]
        if remaining != current:
            execute("security", "list-keychains", "-d", "user", "-s", *remaining)
    print("已创建固定的 Parrot 本地开发签名身份，后续构建复用原证书。")
    print("未安装系统信任根；首次切换身份仍需重新允许 Parrot 的录屏权限。")


if __name__ == "__main__":
    main()
