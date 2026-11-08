"""生成发布资产。仅操作显式输出文件，不发布、不覆盖已有资产。

示例：python tools/update_release.py manifest --installer <exe> --key <pem> --output <新目录>
私钥应存放仓库外，公钥通过 core/software_update.py 随客户端发布。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from packaging.version import Version

from core.app_version import APP_VERSION, REPOSITORY
from core.software_update import ReleaseInfo, verify_installer
from core.startup_upgrade import CURRENT_DATABASE_VERSION


def init_key(path: Path) -> dict:
    repo = Path(__file__).resolve().parent.parent
    if path.resolve().is_relative_to(repo):
        raise ValueError("私钥必须保存在仓库外")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    with path.open("xb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    if os.name == "nt":
        account = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)"], check=True, capture_output=True)
    else:
        path.chmod(0o600)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {hashlib.sha256(public).hexdigest()[:16]: base64.b64encode(public).decode()}


def create_manifest(installer: Path, key_path: Path, output: Path, notes: str, tag: str) -> None:
    if tag != f"v{APP_VERSION}":
        raise ValueError("标签与 core/app_version.py 不一致")
    expected = f"VideoOrganizer-v{APP_VERSION}-setup-win-x64.exe"
    if installer.name != expected:
        raise ValueError(f"安装包应命名为 {expected}")
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise ValueError("必须使用 Ed25519 私钥")
    with installer.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    data = {
        "format": "video-organizer-update", "schema": 1, "platform": "windows-x64",
        "version": APP_VERSION, "channel": "preview" if Version(APP_VERSION).is_prerelease else "stable",
        "published_at": datetime.now(timezone.utc).isoformat(), "notes": notes,
        "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{expected}",
        "filename": expected, "size": installer.stat().st_size,
        "sha256": digest,
        "min_client_version": "1.0", "database_min": 0, "database_max": CURRENT_DATABASE_VERSION,
    }
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    signature = private.sign(raw)
    release = ReleaseInfo.from_signed(raw, signature)
    verify_installer(installer, release)
    # 全新目录保证不覆盖已发布清单。
    output.mkdir(parents=True, exist_ok=False)
    (output / "update.json").write_bytes(raw)
    (output / "update.json.sig").write_bytes(signature)
    (output / f"{expected}.sha256").write_text(f"{data['sha256']}  {expected}\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init-key")
    init.add_argument("--key", type=Path, required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--installer", type=Path, required=True)
    manifest.add_argument("--key", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--notes-file", type=Path, required=True)
    manifest.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.action == "init-key":
        print(json.dumps(init_key(args.key), indent=2))  # 仅输出公钥。
    else:
        create_manifest(args.installer, args.key, args.output, args.notes_file.read_text(encoding="utf-8"), args.tag)


if __name__ == "__main__":
    main()
