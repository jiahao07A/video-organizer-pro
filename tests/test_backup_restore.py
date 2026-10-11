# -*- coding: utf-8 -*-
"""管理数据快照备份与恢复。"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

import core.storage_backup as storage_module
import core.video_organizer_service as service_module
from core.video_organizer_service import SettingsManager, VideoOrganizerService


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VideoOrganizerService:
    settings_path = tmp_path / "settings.json"
    tag_config_path = tmp_path / "tag_config.json"
    monkeypatch.setattr(
        service_module,
        "get_settings_file_path",
        lambda: str(settings_path),
    )
    monkeypatch.setattr(service_module, "TAG_CONFIG_FILE", str(tag_config_path))
    monkeypatch.setattr(service_module, "BACKUP_DIR", str(tmp_path / "backups"))

    service = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _message: None,
        db_path=str(tmp_path / "library.db"),
        results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"),
    )
    SettingsManager.save_settings(service.settings, service.db)
    tag_config_path.write_text(
        json.dumps({"version": "6.0", "tag_groups": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    return service


def _video(path: str, summary: str) -> dict:
    return {
        "path": path,
        "filename": Path(path).name,
        "status": "analyzed",
        "category": "Broll",
        "summary": summary,
        "tags": ["测试"],
        "tag_groups": {},
    }


def test_backup_captures_database_and_configuration_atomically(
    service: VideoOrganizerService,
    tmp_path: Path,
):
    video_path = str(tmp_path / "clip.mp4")
    service.db.upsert_video(_video(video_path, "备份前"))
    settings_path = Path(service_module.get_settings_file_path())
    tag_config_path = Path(service_module.TAG_CONFIG_FILE)
    settings_path.write_text('{"marker": "before"}', encoding="utf-8")
    tag_config_path.write_text('{"marker": "before"}', encoding="utf-8")

    backup_path = service.backup_configuration()

    assert backup_path
    backup = Path(backup_path)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "video-organizer-backup"
    assert manifest["status"] == "complete"
    assert manifest["files"]["database"]["present"] is True
    assert manifest["files"]["settings"]["required"] is True
    assert manifest["files"]["tag_config"]["required"] is True
    assert (backup / "database.sqlite").exists()
    assert (backup / "settings.json").read_text(encoding="utf-8") == '{"marker": "before"}'
    assert (backup / "tag_config.json").read_text(encoding="utf-8") == '{"marker": "before"}'

    service.db.upsert_video(_video(video_path, "备份后"))
    settings_path.write_text('{"marker": "after"}', encoding="utf-8")
    tag_config_path.write_text('{"marker": "after"}', encoding="utf-8")

    assert service.restore_configuration(backup.name) is True
    assert service.db.get_video_by_path(video_path)["summary"] == "备份前"
    assert settings_path.read_text(encoding="utf-8") == '{"marker": "before"}'
    assert tag_config_path.read_text(encoding="utf-8") == '{"marker": "before"}'


def test_missing_configuration_uses_current_memory_fallback(
    service: VideoOrganizerService,
):
    settings_path = Path(service_module.get_settings_file_path())
    tag_config_path = Path(service_module.TAG_CONFIG_FILE)
    settings_path.unlink()
    tag_config_path.unlink()

    backup_path = Path(service.backup_configuration())
    assert backup_path
    backup = Path(backup_path)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["settings"]["required"] is True
    assert manifest["files"]["tag_config"]["required"] is True
    assert json.loads((backup / "settings.json").read_text(encoding="utf-8")) == service.settings
    assert json.loads((backup / "tag_config.json").read_text(encoding="utf-8")) == service.tag_config


@pytest.mark.parametrize(
    ("config_name", "content"),
    [
        ("settings", '{"broken":'),
        ("tag_config", '{"broken":'),
        ("settings", "[]"),
        ("tag_config", "[]"),
    ],
)
def test_corrupt_configuration_rejects_backup(
    service: VideoOrganizerService,
    config_name: str,
    content: str,
):
    path = (
        Path(service_module.get_settings_file_path())
        if config_name == "settings"
        else Path(service_module.TAG_CONFIG_FILE)
    )
    path.write_text(content, encoding="utf-8")

    assert service.backup_configuration() == ""
    backup_dir = Path(service_module.BACKUP_DIR)
    assert not list(backup_dir.glob("backup_*"))
    assert not list(backup_dir.glob(".backup-staging-*"))


def test_invalid_backup_is_rejected_without_changing_current_data(
    service: VideoOrganizerService,
    tmp_path: Path,
):
    video_path = str(tmp_path / "clip.mp4")
    service.db.upsert_video(_video(video_path, "当前数据"))
    backup_path = Path(service.backup_configuration())
    manifest_path = backup_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["database"]["sha256"] = "invalid"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert service.restore_configuration(backup_path.name) is False
    assert service.db.get_video_by_path(video_path)["summary"] == "当前数据"
    assert not (Path(service_module.BACKUP_DIR) / storage_module.RESTORE_JOURNAL_NAME).exists()
    assert backup_path.name not in service.list_backups()

def test_backup_failure_does_not_leave_visible_partial_snapshot(
    service: VideoOrganizerService,
    monkeypatch: pytest.MonkeyPatch,
):
    original_copy2 = storage_module.shutil.copy2
    settings_path = service_module.get_settings_file_path()

    def fail_for_settings(source, destination, *args, **kwargs):
        if os.path.abspath(str(source)) == os.path.abspath(settings_path):
            raise OSError("模拟配置备份写盘失败")
        return original_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(storage_module.shutil, "copy2", fail_for_settings)

    assert service.backup_configuration() == ""
    backup_dir = Path(service_module.BACKUP_DIR)
    assert not list(backup_dir.glob("backup_*"))
    assert not list(backup_dir.glob(".backup-staging-*"))


def test_backup_includes_committed_wal_content(
    service: VideoOrganizerService,
    tmp_path: Path,
):
    video_path = str(tmp_path / "wal-only.mp4")
    connection = sqlite3.connect(service.db.db_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute(
            "INSERT INTO videos (path, filename, status) VALUES (?, ?, ?)",
            (video_path, "wal-only.mp4", "pending"),
        )
        connection.commit()
        backup_path = Path(service.backup_configuration())
    finally:
        connection.close()

    snapshot = sqlite3.connect(backup_path / "database.sqlite")
    try:
        row = snapshot.execute(
            "SELECT status FROM videos WHERE path = ?", (video_path,)
        ).fetchone()
    finally:
        snapshot.close()
    assert row == ("pending",)


def test_restore_failure_rolls_back_current_data(
    service: VideoOrganizerService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video_path = str(tmp_path / "clip.mp4")
    settings_path = Path(service_module.get_settings_file_path())
    tag_config_path = Path(service_module.TAG_CONFIG_FILE)

    service.db.upsert_video(_video(video_path, "目标快照"))
    settings_path.write_text('{"marker": "target"}', encoding="utf-8")
    tag_config_path.write_text('{"marker": "target"}', encoding="utf-8")
    target_backup = Path(service.backup_configuration())

    service.db.upsert_video(_video(video_path, "当前数据"))
    settings_path.write_text('{"marker": "current"}', encoding="utf-8")
    tag_config_path.write_text('{"marker": "current"}', encoding="utf-8")

    original_copy2 = storage_module.shutil.copy2

    def fail_when_applying_target(source, destination, *args, **kwargs):
        if str(source).startswith(str(target_backup)):
            raise OSError("模拟配置恢复写盘失败")
        return original_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(storage_module.shutil, "copy2", fail_when_applying_target)

    assert service.restore_configuration(target_backup.name) is False
    assert service.db.get_video_by_path(video_path)["summary"] == "当前数据"
    assert settings_path.read_text(encoding="utf-8") == '{"marker": "current"}'
    assert tag_config_path.read_text(encoding="utf-8") == '{"marker": "current"}'
    assert not (Path(service_module.BACKUP_DIR) / storage_module.RESTORE_JOURNAL_NAME).exists()


def test_interrupted_restore_is_rolled_back_before_next_service_load(
    service: VideoOrganizerService,
    tmp_path: Path,
):
    video_path = str(tmp_path / "clip.mp4")
    settings_path = Path(service_module.get_settings_file_path())
    tag_config_path = Path(service_module.TAG_CONFIG_FILE)

    service.db.upsert_video(_video(video_path, "可恢复数据"))
    settings_path.write_text('{"marker": "safe"}', encoding="utf-8")
    tag_config_path.write_text(
        '{"version": "6.0", "marker": "safe", "tag_groups": []}',
        encoding="utf-8",
    )
    rollback_backup = Path(service.backup_configuration())

    service.db.upsert_video(_video(video_path, "中断状态"))
    settings_path.write_text('{"marker": "partial"}', encoding="utf-8")
    tag_config_path.write_text('{"marker": "partial"}', encoding="utf-8")
    journal_path = Path(service_module.BACKUP_DIR) / storage_module.RESTORE_JOURNAL_NAME
    journal_path.write_text(
        json.dumps(
            {
                "format": storage_module.BACKUP_FORMAT,
                "version": storage_module.BACKUP_VERSION,
                "status": "applying",
                "source_backup": str(rollback_backup),
                "rollback_backup": str(rollback_backup),
            }
        ),
        encoding="utf-8",
    )

    recovered = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _message: None,
        db_path=service.db.db_path,
        results_json=str(tmp_path / "results-recovered.json"),
        results_csv=str(tmp_path / "results-recovered.csv"),
    )

    assert recovered.db.get_video_by_path(video_path)["summary"] == "可恢复数据"
    assert settings_path.read_text(encoding="utf-8") == '{"marker": "safe"}'
    assert tag_config_path.read_text(encoding="utf-8") == (
        '{"version": "6.0", "marker": "safe", "tag_groups": []}'
    )
    assert not journal_path.exists()
