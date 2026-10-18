# -*- coding: utf-8 -*-
"""启动升级门禁与旧结果首次导入。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import core.startup_upgrade as upgrade_module
import core.video_organizer_service as service_module
from core.startup_upgrade import (
    StartupUpgradeCoordinator,
    StartupUpgradeError,
    UpgradeStatus,
    inspect_database_file,
)
from core.video_organizer_service import DatabaseManager, FileManager, SettingsManager, VideoOrganizerService


def _coordinator(root: Path, *, batch_size: int = 2) -> StartupUpgradeCoordinator:
    return StartupUpgradeCoordinator(
        database_path=str(root / "library.db"),
        json_path=str(root / "video_analysis_results.json"),
        backup_dir=str(root / "backups"),
        settings_path=str(root / "settings.json"),
        tag_config_path=str(root / "tag_config.json"),
        batch_size=batch_size,
    )


def _legacy_row(path: Path, summary: str = "旧摘要") -> dict:
    return {
        "path": str(path),
        "filename": path.name,
        "status": "analyzed",
        "category": "Broll",
        "summary": summary,
        "tags": ["旧标签"],
        "tag_groups": {},
    }


def test_file_manager_constructor_does_not_import_json(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "library.db"))
    source = tmp_path / "results.json"
    source.write_text(json.dumps([_legacy_row(tmp_path / "clip.mp4")]), encoding="utf-8")

    FileManager(db, str(source), str(tmp_path / "results.csv"))

    assert db.get_all_videos() == []


def test_existing_database_wins_and_does_not_read_old_json(tmp_path: Path):
    db_path = tmp_path / "library.db"
    db = DatabaseManager(str(db_path))
    current = tmp_path / "current.mp4"
    db.upsert_video(_legacy_row(current, "数据库中的编辑"))
    source = tmp_path / "video_analysis_results.json"
    source.write_text("不是有效 JSON", encoding="utf-8")

    result = _coordinator(tmp_path).inspect()

    assert result.status is UpgradeStatus.NO_MIGRATION
    assert "不读取" in result.message
    assert inspect_database_file(str(db_path)).exists is True


def test_only_json_requires_confirmation_and_cancel_writes_nothing(tmp_path: Path):
    source = tmp_path / "video_analysis_results.json"
    source.write_text(
        json.dumps([_legacy_row(tmp_path / "clip.mp4")], ensure_ascii=False),
        encoding="utf-8",
    )
    coordinator = _coordinator(tmp_path)

    pending = coordinator.inspect()
    cancelled = coordinator.execute(confirmed=False)

    assert pending.status is UpgradeStatus.NEEDS_CONFIRMATION
    assert cancelled.status is UpgradeStatus.CANCELLED
    assert not (tmp_path / "library.db").exists()
    assert source.exists()


def test_confirmed_import_uses_bounded_batches_and_is_idempotent(tmp_path: Path):
    source = tmp_path / "video_analysis_results.json"
    source.write_text(
        json.dumps(
            [_legacy_row(tmp_path / f"clip-{index}.mp4", str(index)) for index in range(5)],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    progress = []
    coordinator = _coordinator(tmp_path, batch_size=2)

    result = coordinator.execute(
        confirmed=True,
        on_progress=lambda current, total: progress.append((current, total)),
    )
    second = coordinator.inspect()

    assert result.status is UpgradeStatus.COMPLETED
    assert result.report["imported_rows"] == 5
    assert progress[0] == (0, 5)
    assert progress[-1] == (5, 5)
    assert second.status is UpgradeStatus.NO_MIGRATION
    db = DatabaseManager(str(tmp_path / "library.db"))
    assert len(db.get_all_videos()) == 5
    completion = db.get_storage_metadata(upgrade_module.COMPLETION_METADATA_KEY)
    assert completion and completion["status"] == "complete"
    assert not (tmp_path / "backups" / upgrade_module.UPGRADE_JOURNAL_NAME).exists()

    source.write_text(json.dumps([_legacy_row(tmp_path / "new.mp4")]), encoding="utf-8")
    assert coordinator.inspect().status is UpgradeStatus.NO_MIGRATION
    assert db.get_video_by_path(str(tmp_path / "new.mp4")) is None


def test_streaming_parser_handles_record_larger_than_buffer(tmp_path: Path):
    source = tmp_path / "video_analysis_results.json"
    source.write_text(
        json.dumps([_legacy_row(tmp_path / "large.mp4", "x" * 70_000)], ensure_ascii=False),
        encoding="utf-8",
    )

    result = _coordinator(tmp_path).execute(confirmed=True)

    assert result.status is UpgradeStatus.COMPLETED
    db = DatabaseManager(str(tmp_path / "library.db"))
    assert db.get_video_by_path(str(tmp_path / "large.mp4"))["summary"] == "x" * 70_000


def test_interrupted_import_is_blocked_then_retried_after_confirmation(tmp_path: Path):
    source = tmp_path / "video_analysis_results.json"
    source.write_text(json.dumps([_legacy_row(tmp_path / "clip.mp4")]), encoding="utf-8")
    db = DatabaseManager(str(tmp_path / "library.db"))
    db.upsert_video(_legacy_row(tmp_path / "partial.mp4", "半完成"))
    journal_path = tmp_path / "backups" / upgrade_module.UPGRADE_JOURNAL_NAME
    journal_path.parent.mkdir()
    journal_path.write_text(
        json.dumps({"status": "in_progress", "source_sha256": "test"}),
        encoding="utf-8",
    )
    coordinator = _coordinator(tmp_path)

    blocked = coordinator.inspect()
    retried = coordinator.execute(confirmed=True)

    assert blocked.status is UpgradeStatus.FAILED
    assert blocked.report["incomplete_upgrade"] is True
    assert retried.status is UpgradeStatus.COMPLETED
    db2 = DatabaseManager(str(tmp_path / "library.db"))
    assert db2.get_video_by_path(str(tmp_path / "partial.mp4")) is None
    assert db2.get_video_by_path(str(tmp_path / "clip.mp4")) is not None


def test_corrupt_json_is_reported_without_creating_database(tmp_path: Path):
    source = tmp_path / "video_analysis_results.json"
    source.write_text('{"broken":', encoding="utf-8")

    result = _coordinator(tmp_path).inspect()

    assert result.status is UpgradeStatus.FAILED
    assert "旧 JSON 无法读取或格式无效" in result.error
    assert not (tmp_path / "library.db").exists()


def test_duplicate_and_invalid_rows_are_reported_without_paths(tmp_path: Path):
    duplicate = tmp_path / "clip.mp4"
    source = tmp_path / "video_analysis_results.json"
    source.write_text(
        json.dumps([_legacy_row(duplicate), _legacy_row(duplicate), {"filename": "missing"}, "bad"]),
        encoding="utf-8",
    )
    result = _coordinator(tmp_path).inspect()

    assert result.status is UpgradeStatus.NEEDS_CONFIRMATION
    assert result.report["valid_rows"] == 1
    assert result.report["duplicate_rows"] == [{"row": 2, "reason": "duplicate_path"}]
    assert result.report["invalid_rows"] == [
        {"row": 3, "reason": "missing_path"},
        {"row": 4, "reason": "row_not_object"},
    ]
    assert all("path" not in row for row in result.report["duplicate_rows"] + result.report["invalid_rows"])


def test_corrupt_database_is_rejected_without_creating_empty_database(tmp_path: Path):
    db_path = tmp_path / "library.db"
    db_path.write_bytes(b"not a sqlite database")
    source = tmp_path / "video_analysis_results.json"
    source.write_text(json.dumps([_legacy_row(tmp_path / "clip.mp4")]), encoding="utf-8")

    result = _coordinator(tmp_path).inspect()

    assert result.status is UpgradeStatus.FAILED
    assert "拒绝降级为空库" in result.error
    assert db_path.read_bytes() == b"not a sqlite database"


def test_unknown_database_version_is_rejected(tmp_path: Path):
    db_path = tmp_path / "library.db"
    DatabaseManager(str(db_path))
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 99")
        connection.commit()

    result = _coordinator(tmp_path).inspect()

    assert result.status is UpgradeStatus.FAILED
    assert "高于当前程序支持" in result.error


def test_failed_import_removes_half_created_database_and_keeps_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "video_analysis_results.json"
    source.write_text(json.dumps([_legacy_row(tmp_path / "clip.mp4")]), encoding="utf-8")

    def fail_batch(self, _rows):
        raise RuntimeError("模拟批次写入失败")

    monkeypatch.setattr(service_module.DatabaseManager, "upsert_videos_batch", fail_batch)
    result = _coordinator(tmp_path).execute(confirmed=True)

    assert result.status is UpgradeStatus.FAILED
    assert "模拟批次写入失败" in result.error
    assert source.exists()
    assert not (tmp_path / "library.db").exists()


def test_backup_failure_keeps_source_and_allows_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "video_analysis_results.json"
    source.write_text(json.dumps([_legacy_row(tmp_path / "clip.mp4")]), encoding="utf-8")

    def fail_backup(self):
        raise RuntimeError("模拟快照失败")

    monkeypatch.setattr(upgrade_module.StorageBackupManager, "create_backup", fail_backup)
    result = _coordinator(tmp_path).execute(confirmed=True)

    assert result.status is UpgradeStatus.FAILED
    assert "模拟快照失败" in result.error
    assert source.exists()
    assert not (tmp_path / "library.db").exists()


def test_service_startup_gate_exposes_confirmation_and_blocks_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    settings_path = tmp_path / "settings.json"
    tag_config_path = tmp_path / "tag_config.json"
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(service_module, "get_settings_file_path", lambda: str(settings_path))
    monkeypatch.setattr(service_module, "TAG_CONFIG_FILE", str(tag_config_path))
    monkeypatch.setattr(service_module, "BACKUP_DIR", str(backup_dir))
    source = tmp_path / "results.json"
    source.write_text(json.dumps([_legacy_row(tmp_path / "clip.mp4")]), encoding="utf-8")
    kwargs = {
        "settings": SettingsManager.deep_copy_defaults(),
        "on_log": lambda _message: None,
        "db_path": str(tmp_path / "library.db"),
        "results_json": str(source),
        "results_csv": str(tmp_path / "results.csv"),
    }

    pending_result = VideoOrganizerService.inspect_startup_upgrade(
        db_path=kwargs["db_path"], results_json=kwargs["results_json"]
    )
    assert pending_result.status is UpgradeStatus.NEEDS_CONFIRMATION
    with pytest.raises(StartupUpgradeError) as pending:
        VideoOrganizerService(**kwargs)
    assert pending.value.result.status is UpgradeStatus.NEEDS_CONFIRMATION
    with pytest.raises(StartupUpgradeError) as cancelled:
        VideoOrganizerService(**kwargs, startup_upgrade_confirmed=False)
    assert cancelled.value.result.status is UpgradeStatus.CANCELLED
    assert not (tmp_path / "library.db").exists()

    service = VideoOrganizerService(**kwargs, startup_upgrade_confirmed=True)
    assert service.db.get_video_by_path(str(tmp_path / "clip.mp4")) is not None
