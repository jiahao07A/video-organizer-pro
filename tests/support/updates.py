"""共享隔离测试辅助。"""
import base64
import hashlib
import json
from types import SimpleNamespace
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import core.software_update as updates
from core.app_version import REPOSITORY
from core.startup_upgrade import CURRENT_DATABASE_VERSION


@pytest.fixture
def signed(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(updates, "TRUSTED_KEYS", {"test": base64.b64encode(public).decode()})
    payload = b"test installer bytes"
    release_version = "0.0.3"
    name = f"VideoOrganizer-v{release_version}-setup-win-x64.exe"
    data = dict(
        format="video-organizer-update", schema=1, platform="windows-x64",
        version=release_version, channel="stable", published_at="2026-11-07", notes="修复与优化",
        filename=name, url=f"https://github.com/{REPOSITORY}/releases/download/v{release_version}/{name}",
        size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), min_client_version="1.0",
        database_min=0, database_max=CURRENT_DATABASE_VERSION,
    )
    def encode(**changes):
        raw = json.dumps(data | changes, ensure_ascii=False).encode()
        return raw, private.sign(raw)
    installer = tmp_path / name
    installer.write_bytes(payload)
    return SimpleNamespace(private=private, data=data, encode=encode, installer=installer)

def candidate(tag, *, prerelease=False, draft=False, complete=True):
    names = [f"VideoOrganizer-{tag}-setup-win-x64.exe", "update.json", "update.json.sig"]
    return dict(tag_name=tag, prerelease=prerelease, draft=draft, assets=[
        {"name": name, "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"}
        for name in (names if complete else names[:1])
    ])
