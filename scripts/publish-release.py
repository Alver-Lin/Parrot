"""Create a GitHub Release and upload the macOS pkg (uses git credential for github.com)."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "Alver-Lin/Parrot"
TAG = "v1.0.3"
PKG = ROOT / "dist/release/Parrot-1.0.3-arm64.pkg"
SHA = ROOT / "dist/release/Parrot-1.0.3-arm64.pkg.sha256"

BODY = """## Parrot v1.0.3（macOS）

Apple Silicon（arm64）安装包。

### 更新内容
- 修复从本机构建切换到安装包后学习记录看起来被清空的问题
- 安装版与开发版继续使用独立数据目录；安装版仅在本地库为空时从旧 `.data` 导入一次
- 之后重装或升级（含 1.0.1 → 1.0.3）只使用 `~/Library/Application Support/Parrot`，不会再用开发目录覆盖

### 安装
1. 下载下方 `Parrot-1.0.3-arm64.pkg`
2. 双击安装到「应用程序」
3. 从启动台打开 Parrot

升级同一安装包时，会先退出并移除旧的 `/Applications/Parrot.app`，再写入新版本。个人数据保留在 `~/Library/Application Support/Parrot`。

### 说明
- 系统要求：macOS 13+
- 识别模型首次使用时在设置中下载
- 当前为开发签名、未经 Apple 公证；若无法打开，请在「系统设置 → 隐私与安全性」允许，或按住 Control 点击安装包后选择「打开」

引导下载页：仓库内 `docs/download.html`

### 校验
见附件 `Parrot-1.0.3-arm64.pkg.sha256`
"""


def github_token() -> str:
    filled = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    for line in filled.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("未找到 github.com 凭据，请先 git push 登录。")


def api(token: str, method: str, url: str, data: bytes | None = None, content_type: str | None = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Parrot-release-script",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            raw = response.read()
            return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode()
        raise SystemExit(f"GitHub API {error.code}: {detail}") from error


def ensure_release(token: str) -> dict:
    try:
        return api(
            token,
            "POST",
            f"https://api.github.com/repos/{REPO}/releases",
            data=json.dumps(
                {
                    "tag_name": TAG,
                    "name": "Parrot v1.0.3",
                    "body": BODY,
                    "draft": False,
                    "prerelease": False,
                }
            ).encode(),
            content_type="application/json",
        )
    except SystemExit as exc:
        message = str(exc)
        if "422" in message and "already_exists" in message:
            return api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}")
        raise


def upload_asset(token: str, release: dict, path: Path):
    name = path.name
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            api(token, "DELETE", asset["url"])
    print(f"上传 {name}（{path.stat().st_size // (1024 * 1024)} MB）…")
    upload_url = release["upload_url"].split("{", 1)[0] + f"?name={name}"
    with path.open("rb") as handle:
        api(
            token,
            "POST",
            upload_url,
            data=handle.read(),
            content_type="application/octet-stream",
        )
    print(f"已上传：{name}")


def main():
    if not PKG.is_file() or not SHA.is_file():
        raise SystemExit("缺少 dist/release 下的 pkg 或 sha256，请先运行 build-release.py。")
    token = github_token()
    release = ensure_release(token)
    print(release.get("html_url", f"https://github.com/{REPO}/releases/tag/{TAG}"))
    upload_asset(token, release, PKG)
    upload_asset(token, release, SHA)
    print(f"发布完成： https://github.com/{REPO}/releases/tag/{TAG}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
