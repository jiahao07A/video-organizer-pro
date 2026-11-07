"""更新安全边界、数据保护与发布资产回归。"""
import base64
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.app_version import APP_VERSION, REPOSITORY
from core.data_directory import DataDirectoryManager, DataDirectoryState
import core.software_update as updates
from core.software_update import ReleaseInfo, UpdateError, UpdateSession, release_candidates, verify_installer


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
        database_min=0, database_max=1,
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


def test_version_order_and_legacy_rc_flags():
    releases = [candidate("v0.0.3-rc1"), candidate("v0.0.10"), candidate("v0.0.4"),
                candidate("v0.0.11", draft=True), candidate("v0.0.12", complete=False),
                candidate("v0.0.13", prerelease=True)]
    assert [r["tag_name"] for r in release_candidates(releases)] == ["v0.0.10", "v0.0.4"]
    assert [r["tag_name"] for r in release_candidates(releases, preview=True)][-1] == "v0.0.3-rc1"
    assert release_candidates([candidate("v0.0.0")], preview=True) == []


@pytest.mark.parametrize("url", [
    "http://github.com/a", "https://github.com.evil.test/a", "https://user@github.com/a",
    "https://github.com/other/repo/releases/download/a/b", "https://api.github.com/user",
    f"https://github.com/{REPOSITORY}/releases/download/../../evil",
    "https://release-assets.githubusercontent.com:444/a",
])
def test_untrusted_urls_rejected(url):
    with pytest.raises((UpdateError, ValueError)):
        updates.validate_url(url, api=True)


def test_signed_installer_and_raw_bytes(signed):
    raw, signature = signed.encode()
    release = ReleaseInfo.from_signed(raw, signature)
    verify_installer(signed.installer, release)
    with pytest.raises(UpdateError, match="签名"):
        ReleaseInfo.from_signed(raw + b" ", signature)
    signed.installer.write_bytes(b"X" * release.size)
    with pytest.raises(UpdateError, match="SHA-256"):
        verify_installer(signed.installer, release)


@pytest.mark.parametrize("changes", [
    {"platform": "linux"}, {"size": -1}, {"size": True}, {"sha256": "x"},
    {"url": "https://evil.test/file.exe"}, {"filename": "../file.exe"},
    {"min_client_version": "9.0"}, {"database_min": 3, "database_max": 1},
    {"channel": "nightly"}, {"version": "../evil"}, {"notes": None},
])
def test_invalid_signed_manifest_rejected(signed, changes):
    raw, signature = signed.encode(**changes)
    with pytest.raises(UpdateError):
        ReleaseInfo.from_signed(raw, signature)


def test_no_trust_and_missing_installer(signed, monkeypatch):
    raw, signature = signed.encode()
    release = ReleaseInfo.from_signed(raw, signature)
    with pytest.raises(UpdateError):
        verify_installer(signed.installer.with_name("missing.exe"), release)
    monkeypatch.setattr(updates, "TRUSTED_KEYS", {})
    with pytest.raises(UpdateError, match="签名"):
        ReleaseInfo.from_signed(raw, signature)


def test_session_only_succeeds_for_new_version_and_original_database(tmp_path):
    database = tmp_path / "video_organizer.db"
    database.write_bytes(b"placeholder")
    session = UpdateSession("0.0.3", APP_VERSION, str(tmp_path), str(tmp_path), str(database), "backup", {})
    assert "未完成" in session.reconcile(APP_VERSION, str(tmp_path))
    assert session.phase == "incomplete"
    session.save(tmp_path)
    restored = UpdateSession.load(tmp_path)
    with pytest.raises(UpdateError, match="空素材库"):
        restored.reconcile("0.0.3", str(tmp_path / "other"))
    assert "更新完成" in restored.reconcile("0.0.3", str(tmp_path))
    assert restored.phase == "completed"
    database.unlink()
    with pytest.raises(UpdateError):
        session.reconcile("0.0.3", str(tmp_path))


def test_prepare_backup_failure_does_not_record_install(signed, tmp_path, monkeypatch):
    from core import update_runtime
    from core.video_organizer_service import VideoOrganizerService
    service = VideoOrganizerService(db_path=str(tmp_path / "library.db"), results_json=str(tmp_path / "results.json"))
    release = ReleaseInfo.from_signed(*signed.encode())
    manager = service._storage_backup_manager()
    monkeypatch.setattr(manager, "create_backup", lambda: (_ for _ in ()).throw(UpdateError("备份失败")))
    monkeypatch.setattr(service, "_storage_backup_manager", lambda: manager)
    monkeypatch.setattr(update_runtime, "application_directory", lambda: tmp_path)
    with pytest.raises(UpdateError, match="备份失败"):
        update_runtime.prepare_install(service, release, signed.installer, tmp_path)
    assert not (tmp_path / "session.json").exists()


def test_prepare_preserves_custom_data_and_snapshots(signed, tmp_path, monkeypatch):
    from core import update_runtime
    from core.video_organizer_service import VideoOrganizerService
    service = VideoOrganizerService(db_path=str(tmp_path / "library.db"), results_json=str(tmp_path / "results.json"))
    release = ReleaseInfo.from_signed(*signed.encode())
    monkeypatch.setattr(update_runtime, "application_directory", lambda: tmp_path)
    session = update_runtime.prepare_install(service, release, signed.installer, tmp_path)
    assert Path(session.database_path) == Path(service.db.db_path)
    assert Path(session.backup_path).is_dir()
    service._storage_backup_manager().validate_backup(session.backup_path)
    assert UpdateSession.load(tmp_path).phase == "prepared"


def test_space_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.shutil, "disk_usage", lambda _: SimpleNamespace(free=5))
    with pytest.raises(UpdateError, match="空间"):
        updates.require_space(tmp_path, 6)


def test_launch_error_keeps_recovery(signed, tmp_path, monkeypatch):
    from core import update_runtime
    session = UpdateSession("0.0.3", APP_VERSION, str(tmp_path), str(tmp_path), "db", "backup", {})
    def fail(*args, **kwargs):
        raise OSError("blocked")
    monkeypatch.setattr(update_runtime.subprocess, "Popen", fail)
    with pytest.raises(UpdateError):
        update_runtime.launch_installer(signed.installer, session, tmp_path)
    assert UpdateSession.load(tmp_path).phase == "incomplete"


def test_release_tool_rejects_wrong_tag_and_signs(signed, tmp_path, monkeypatch):
    import tools.update_release as tool
    monkeypatch.setattr(tool, "APP_VERSION", "0.0.3")
    key = tmp_path / "test-key.pem"
    key.write_bytes(signed.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    output = tmp_path / "assets"
    with pytest.raises(ValueError, match="标签"):
        tool.create_manifest(signed.installer, key, output, "更新", "v0.0.4")
    tool.create_manifest(signed.installer, key, output, "更新", "v0.0.3")
    release = ReleaseInfo.from_signed((output / "update.json").read_bytes(), (output / "update.json.sig").read_bytes())
    verify_installer(signed.installer, release)
    with pytest.raises(FileExistsError):
        tool.create_manifest(signed.installer, key, output, "更新", "v0.0.3")


def test_pending_startup_blocks_missing_or_relocated_original_library(tmp_path, monkeypatch):
    from core import update_runtime
    database = tmp_path / "video_organizer.db"
    database.write_bytes(b"existing")
    session = UpdateSession("0.0.3", APP_VERSION, str(tmp_path), str(tmp_path), str(database), "backup", {}, phase="launched")
    session.save(tmp_path)
    monkeypatch.setattr(update_runtime, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(update_runtime, "DataDirectoryManager", lambda: SimpleNamespace(inspect=lambda: SimpleNamespace(active_root=str(tmp_path), state=DataDirectoryState.ACTIVE)))
    assert update_runtime.pending_startup_session(tmp_path).phase == "launched"
    database.unlink()
    with pytest.raises(UpdateError, match="空素材库"):
        update_runtime.pending_startup_session(tmp_path)
    assert not database.exists()


def test_another_running_instance_blocks_preparation(monkeypatch):
    from core import update_runtime
    import os
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    other = SimpleNamespace(pid=os.getpid() + 9999, info={"exe": sys.executable, "name": Path(sys.executable).name})
    monkeypatch.setattr(update_runtime.psutil, "process_iter", lambda _: [other])
    with pytest.raises(UpdateError, match="其他应用实例"):
        update_runtime.ensure_no_other_instances()


def test_installer_launch_uses_explicit_directory_and_no_force_close(tmp_path, monkeypatch):
    from core import update_runtime
    session = UpdateSession("0.0.3", APP_VERSION, str(tmp_path / "应用 目录"), "data", "db", "backup", {})
    commands = []
    monkeypatch.setattr(update_runtime.subprocess, "Popen", lambda command, **kwargs: commands.append(command))
    update_runtime.launch_installer(tmp_path / "installer.exe", session, tmp_path)
    assert f"/DIR={session.application_directory}" in commands[0]
    assert "/NOCLOSEAPPLICATIONS" in commands[0]
    assert UpdateSession.load(tmp_path).phase == "launched"


def test_cancelled_update_does_not_pin_future_data_directory(tmp_path, monkeypatch):
    from core import update_runtime
    original = tmp_path / "original"
    original.mkdir()
    database = original / "video_organizer.db"
    database.write_bytes(b"original")
    session = UpdateSession("0.0.3", APP_VERSION, str(tmp_path), str(original), str(database), "backup", {}, phase="launched")
    assert "未完成" in session.reconcile(APP_VERSION, str(original))
    session.save(tmp_path)
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "video_organizer.db").write_bytes(b"another valid library")
    monkeypatch.setattr(update_runtime, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(update_runtime, "DataDirectoryManager", lambda: SimpleNamespace(inspect=lambda: SimpleNamespace(active_root=str(selected), state=DataDirectoryState.ACTIVE)))
    result = update_runtime.pending_startup_session(tmp_path)
    assert not result.is_pending


def test_pending_update_blocks_corrupted_original_database_before_directory_selection(tmp_path, monkeypatch):
    from core import update_runtime
    original = tmp_path / "original"
    original.mkdir()
    database = original / "video_organizer.db"
    database.write_bytes(b"corrupted SQLite")
    manager = DataDirectoryManager(application_directory=str(tmp_path))
    manager.write_locator(str(original))
    session = UpdateSession("0.0.3", APP_VERSION, str(tmp_path), str(original), str(database), "backup", {}, phase="launched")
    session.save(tmp_path)
    monkeypatch.setattr(update_runtime, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(update_runtime, "DataDirectoryManager", lambda: manager)
    with pytest.raises(UpdateError, match="检查失败"):
        update_runtime.pending_startup_session(tmp_path)
    assert not (tmp_path / "data").exists()
