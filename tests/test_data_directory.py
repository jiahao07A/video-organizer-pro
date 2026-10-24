# -*- coding: utf-8 -*-
"""统一数据目录与安全搬迁测试。"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.data_directory import (
    DataDirectoryConflictError,
    DataDirectoryError,
    DataDirectoryManager,
    DataDirectoryState,
)


def _make_database(path: Path, summary: str = "当前") -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, path TEXT, thumbnail TEXT)")
    connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO videos(path, thumbnail) VALUES (?, ?)", ("clip.mp4", ".thumbnails/thumb.webp"))
    connection.commit()
    connection.close()


def test_default_directory_uses_application_location_not_process_cwd(tmp_path: Path):
    manager = DataDirectoryManager(application_directory=str(tmp_path / "app"))

    assert manager.default_paths().root == str((tmp_path / "app" / "data").resolve())
    assert manager.default_paths().database.endswith("data\\video_organizer.db") or manager.default_paths().database.endswith("data/video_organizer.db")


def test_repository_backup_sources_are_not_legacy_management_data(tmp_path: Path):
    app = tmp_path / "app"
    obsolete = app / "backups" / "obsolete_20260211"
    obsolete.mkdir(parents=True)
    (obsolete / "legacy_helper.py").write_text("pass\n", encoding="utf-8")
    thumbnails = app / ".thumbnails"
    thumbnails.mkdir()
    (thumbnails / "orphan.webp").write_bytes(b"orphan")

    inspection = DataDirectoryManager(application_directory=str(app)).inspect()

    assert inspection.state is DataDirectoryState.DEFAULT_NEW
    assert inspection.legacy_items == ()


def test_locator_stores_only_required_state_and_survives_working_directory_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app = tmp_path / "app"
    selected = tmp_path / "selected"
    manager = DataDirectoryManager(application_directory=str(app))
    manager.activate_empty(str(selected))

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    manager2 = DataDirectoryManager(application_directory=str(app))
    assert manager2.read_locator() == str(selected.resolve())
    assert manager2.inspect().active_root == str(selected.resolve())
    payload = json.loads((app / ".video-organizer-data.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "format",
        "version",
        "data_root",
        "database_initialized",
        "updated_at",
    }
    assert payload["database_initialized"] is False
    assert "settings" not in payload
    assert "api" not in payload


def test_corrupt_locator_is_reported_as_invalid_for_alternate_selection(
    tmp_path: Path,
):
    app = tmp_path / "app"
    app.mkdir()
    locator = app / ".video-organizer-data.json"
    locator.write_text("{broken", encoding="utf-8")

    inspection = DataDirectoryManager(application_directory=str(app)).inspect()

    assert inspection.state is DataDirectoryState.INVALID
    assert "定位记录无法读取" in inspection.message


def test_incomplete_default_locator_restores_legacy_migration_gate(
    tmp_path: Path,
):
    app = tmp_path / "app"
    app.mkdir()
    manager = DataDirectoryManager(application_directory=str(app))
    manager.write_locator(str(app / "data"), database_initialized=True)
    (app / "video_organizer.db").write_bytes(b"legacy")

    inspection = manager.inspect()

    assert inspection.state is DataDirectoryState.LEGACY
    assert "video_organizer.db" in inspection.legacy_items


def test_legacy_layout_is_reported_without_opening_old_database(tmp_path: Path):
    (tmp_path / "video_organizer.db").write_bytes(b"legacy")
    manager = DataDirectoryManager(application_directory=str(tmp_path))

    inspection = manager.inspect()

    assert inspection.state is DataDirectoryState.LEGACY
    assert "video_organizer.db" in inspection.legacy_items
    assert not (tmp_path / "data").exists()


def test_legacy_layout_is_reported_before_unlocated_default_data(tmp_path: Path):
    (tmp_path / "video_organizer.db").write_bytes(b"legacy")
    default_root = tmp_path / "data"
    default_root.mkdir()
    _make_database(default_root / "video_organizer.db")

    inspection = DataDirectoryManager(application_directory=str(tmp_path)).inspect()

    assert inspection.state is DataDirectoryState.LEGACY
    assert "video_organizer.db" in inspection.legacy_items


def test_legacy_migration_copies_database_config_and_thumbnail_without_moving_source(
    tmp_path: Path,
):
    source = tmp_path / "app"
    source.mkdir()
    (source / "settings.json").write_text('{"marker": "safe"}', encoding="utf-8")
    (source / "tag_config.json").write_text('{"tag_groups": []}', encoding="utf-8")
    (source / ".thumbnails").mkdir()
    (source / ".thumbnails" / "thumb.webp").write_bytes(b"thumbnail")
    _make_database(source / "video_organizer.db")
    manager = DataDirectoryManager(application_directory=str(source))

    target = manager.migrate_legacy()

    assert Path(target.database).exists()
    assert Path(target.settings).read_text(encoding="utf-8") == '{"marker": "safe"}'
    assert Path(target.thumbnails, "thumb.webp").read_bytes() == b"thumbnail"
    assert (source / "video_organizer.db").exists()
    assert (source / ".thumbnails" / "thumb.webp").exists()
    assert manager.read_locator() == str((source / "data").resolve())
    connection = sqlite3.connect(target.database)
    try:
        assert connection.execute("SELECT path, thumbnail FROM videos").fetchone() == (
            "clip.mp4", ".thumbnails/thumb.webp"
        )
    finally:
        connection.close()


def test_legacy_layout_without_database_migrates_for_first_json_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "app"
    source.mkdir()
    legacy_json = source / "video_analysis_results.json"
    legacy_json.write_text("[]", encoding="utf-8")
    (source / "settings.json").write_text("{}", encoding="utf-8")
    manager = DataDirectoryManager(application_directory=str(source))

    target = manager.migrate_legacy()

    assert Path(target.results_json).read_text(encoding="utf-8") == "[]"
    assert legacy_json.exists()
    assert Path(target.backups, "legacy-source-snapshot", "settings.json").read_text(
        encoding="utf-8"
    ) == "{}"
    assert Path(
        target.backups, "legacy-source-snapshot", "video_analysis_results.json"
    ).read_text(encoding="utf-8") == "[]"
    snapshot_manifest = json.loads(
        Path(
            target.backups,
            "legacy-source-snapshot",
            "manifest.json",
        ).read_text(encoding="utf-8")
    )
    assert snapshot_manifest["status"] == "complete"
    assert snapshot_manifest["files"]
    assert manager.read_locator() == str(Path(target.root).resolve())
    assert manager.inspect().state is DataDirectoryState.ACTIVE

    import core.video_organizer_service as service_module
    from core.startup_upgrade import UpgradeStatus
    from core.video_organizer_service import VideoOrganizerService

    monkeypatch.setattr(service_module, "_service_data_manager", lambda: manager)
    pending = VideoOrganizerService.inspect_startup_upgrade(
        include_import_report=False
    )
    assert pending.status is UpgradeStatus.NEEDS_CONFIRMATION

    imported = VideoOrganizerService.run_startup_upgrade(confirmed=True)
    assert imported.status is UpgradeStatus.COMPLETED
    service = VideoOrganizerService(on_log=lambda _message: None)
    assert Path(target.database).is_file()
    assert manager.inspect().state is DataDirectoryState.ACTIVE
    locator = json.loads(Path(manager.locator_path).read_text(encoding="utf-8"))
    assert locator["database_initialized"] is True


def test_existing_target_is_not_implicitly_merged_or_overwritten(tmp_path: Path):
    source = tmp_path / "app"
    source.mkdir()
    (source / "video_organizer.db").write_bytes(b"legacy")
    target = source / "data"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    manager = DataDirectoryManager(application_directory=str(source))

    with pytest.raises(DataDirectoryConflictError):
        manager.migrate_legacy()

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (source / ".video-organizer-data.json").exists()


def test_partial_management_directory_is_not_openable(tmp_path: Path):
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "settings.json").write_text("{}", encoding="utf-8")
    manager = DataDirectoryManager(application_directory=str(tmp_path / "app"))

    assert manager.has_management_data(str(selected)) is True
    assert manager.has_openable_management_data(str(selected)) is False
    with pytest.raises(DataDirectoryError, match="没有可打开"):
        manager.open_existing(str(selected))


def test_existing_management_file_permission_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.data_directory as data_directory_module

    selected = tmp_path / "selected"
    selected.mkdir()
    _make_database(selected / "video_organizer.db")
    settings = selected / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    manager = DataDirectoryManager(application_directory=str(tmp_path / "app"))
    original_access = data_directory_module.os.access

    def deny_settings(path, mode):
        if os.path.abspath(path) == os.path.abspath(settings) and mode == os.W_OK:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(data_directory_module.os, "access", deny_settings)

    inspection = manager.__class__(application_directory=str(tmp_path / "app"))
    inspection.write_locator(str(selected))
    result = inspection.inspect()

    assert result.state is DataDirectoryState.INVALID
    assert "设置文件不可写" in result.message


def test_migration_rejects_target_inside_source_thumbnail_directory(
    tmp_path: Path,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "video_organizer.db").write_bytes(b"legacy")
    thumbnails = source / ".thumbnails"
    thumbnails.mkdir()
    manager = DataDirectoryManager(application_directory=str(tmp_path / "app"))

    with pytest.raises(DataDirectoryError, match="缩略图目录内部"):
        manager._copy_and_activate(
            source_root=str(source),
            target_root=str(thumbnails / "nested"),
        )


def test_migration_validates_integrity_and_thumbnail_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "app"
    source.mkdir()
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    thumbnails = source / ".thumbnails"
    thumbnails.mkdir()
    (thumbnails / "thumb.webp").write_bytes(b"thumbnail")
    _make_database(source / "video_organizer.db")
    calls = []

    import core.startup_upgrade as startup_upgrade
    original_inspect = startup_upgrade.inspect_database_file

    def record_inspect(path, *, verify_integrity=False):
        calls.append(verify_integrity)
        return original_inspect(path, verify_integrity=verify_integrity)

    monkeypatch.setattr(startup_upgrade, "inspect_database_file", record_inspect)
    manager = DataDirectoryManager(application_directory=str(source))

    target = manager.migrate_legacy()

    assert True in calls
    assert list(Path(target.backups).glob("backup_*"))


def test_migration_recreates_preexisting_empty_target_when_activation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.data_directory as data_directory_module

    source = tmp_path / "app"
    source.mkdir()
    _make_database(source / "video_organizer.db")
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    (source / ".thumbnails").mkdir()
    (source / ".thumbnails" / "thumb.webp").write_bytes(b"thumbnail")
    target = tmp_path / "external" / "data"
    target.mkdir(parents=True)
    manager = DataDirectoryManager(application_directory=str(source))

    original_replace = data_directory_module.os.replace

    def fail_replace(source_path, destination_path):
        if os.path.normcase(os.path.abspath(destination_path)) == os.path.normcase(
            str(target.resolve())
        ):
            raise OSError("模拟激活失败")
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(data_directory_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="模拟激活失败"):
        manager.migrate_legacy(str(target))

    assert target.is_dir()
    assert not (target / "video_organizer.db").exists()
    assert (source / "video_organizer.db").exists()


def test_locator_failure_rolls_back_activated_migration_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.data_directory as data_directory_module

    source = tmp_path / "app"
    source.mkdir()
    _make_database(source / "video_organizer.db")
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    (source / ".thumbnails").mkdir()
    (source / ".thumbnails" / "thumb.webp").write_bytes(b"thumbnail")
    manager = DataDirectoryManager(application_directory=str(source))
    target = tmp_path / "external" / "data"

    def fail_locator(*_args, **_kwargs):
        raise OSError("locator is read-only")

    monkeypatch.setattr(data_directory_module, "write_json_atomic", fail_locator)

    with pytest.raises(DataDirectoryError, match="定位记录"):
        manager.migrate_legacy(str(target))

    assert not target.exists()
    assert (source / "video_organizer.db").exists()


def test_migration_preserves_stable_ids_analysis_results_and_source(
    tmp_path: Path,
):
    source = tmp_path / "app"
    source.mkdir()
    (source / "settings.json").write_text('{"marker": "safe"}', encoding="utf-8")
    (source / "tag_config.json").write_text('{"tag_groups": []}', encoding="utf-8")
    (source / ".thumbnails").mkdir()
    (source / ".thumbnails" / "thumb.webp").write_bytes(b"thumbnail")
    (source / "video_analysis_results.json").write_text(
        '[{"path": "clip.mp4", "summary": "分析结果"}]',
        encoding="utf-8",
    )
    _make_database(source / "video_organizer.db")
    manager = DataDirectoryManager(application_directory=str(source))

    target = manager.migrate_legacy()

    connection = sqlite3.connect(target.database)
    try:
        assert connection.execute("SELECT id, path FROM videos").fetchone() == (1, "clip.mp4")
    finally:
        connection.close()
    assert Path(target.results_json).read_text(encoding="utf-8") == (
        '[{"path": "clip.mp4", "summary": "分析结果"}]'
    )
    assert Path(target.settings).read_text(encoding="utf-8") == '{"marker": "safe"}'
    assert Path(target.thumbnails, "thumb.webp").read_bytes() == b"thumbnail"
    assert (source / "video_organizer.db").exists()


def test_migration_fails_before_activation_when_backup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.data_directory as data_directory_module
    from core.storage_backup import StorageBackupError

    source = tmp_path / "app"
    source.mkdir()
    _make_database(source / "video_organizer.db")
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    (source / ".thumbnails").mkdir()
    (source / ".thumbnails" / "thumb.webp").write_bytes(b"thumbnail")
    manager = DataDirectoryManager(application_directory=str(source))

    def fail_backup(self):
        raise StorageBackupError("模拟备份失败")

    monkeypatch.setattr(
        data_directory_module.StorageBackupManager,
        "create_backup",
        fail_backup,
    )

    with pytest.raises(DataDirectoryError, match="迁移前备份失败"):
        manager.migrate_legacy()

    assert not (source / "data").exists()
    assert manager.read_locator() is None


def test_migration_fails_before_activation_when_database_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "app"
    source.mkdir()
    _make_database(source / "video_organizer.db")
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    (source / ".thumbnails").mkdir()
    (source / ".thumbnails" / "thumb.webp").write_bytes(b"thumbnail")
    manager = DataDirectoryManager(application_directory=str(source))

    def fail_copy(*_args, **_kwargs):
        raise DataDirectoryError("模拟数据库复制失败")

    monkeypatch.setattr(DataDirectoryManager, "_copy_sqlite", staticmethod(fail_copy))

    with pytest.raises(DataDirectoryError, match="数据库复制失败"):
        manager.migrate_legacy()

    assert not (source / "data").exists()
    assert manager.read_locator() is None


def test_migration_rejects_missing_thumbnail_reference(tmp_path: Path):
    source = tmp_path / "app"
    source.mkdir()
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    _make_database(source / "video_organizer.db")
    connection = sqlite3.connect(source / "video_organizer.db")
    connection.execute(
        "UPDATE videos SET thumbnail = ? WHERE id = 1",
        (".thumbnails/missing.webp",),
    )
    connection.commit()
    connection.close()
    manager = DataDirectoryManager(application_directory=str(source))

    with pytest.raises(DataDirectoryError, match="缩略图引用无效"):
        manager.migrate_legacy()

    assert not (source / "data" / "video_organizer.db").exists()
    assert manager.read_locator() is None


@pytest.mark.parametrize("absolute_reference", [False, True])
def test_migration_rejects_thumbnail_reference_outside_target_directory(
    tmp_path: Path,
    absolute_reference: bool,
):
    source = tmp_path / "app"
    source.mkdir()
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    outside_thumbnail = tmp_path / "outside.webp"
    outside_thumbnail.write_bytes(b"outside thumbnail")
    _make_database(source / "video_organizer.db")
    connection = sqlite3.connect(source / "video_organizer.db")
    stored_reference = (
        str(outside_thumbnail) if absolute_reference else "../outside.webp"
    )
    connection.execute(
        "UPDATE videos SET thumbnail = ? WHERE id = 1",
        (stored_reference,),
    )
    connection.commit()
    connection.close()
    manager = DataDirectoryManager(application_directory=str(source))

    with pytest.raises(DataDirectoryError, match="未使用目标数据目录内的相对路径"):
        manager.migrate_legacy()

    assert not (source / "data" / "video_organizer.db").exists()
    assert manager.read_locator() is None


def test_empty_directory_can_be_selected_then_initialized_by_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.video_organizer_service as service_module
    from core.video_organizer_service import VideoOrganizerService

    app = tmp_path / "app"
    selected = tmp_path / "selected"
    manager = DataDirectoryManager(application_directory=str(app))

    paths = manager.activate_empty(str(selected))

    assert paths.root == str(selected.resolve())
    assert manager.read_locator() == str(selected.resolve())
    assert manager.inspect().state is DataDirectoryState.ACTIVE

    monkeypatch.setattr(service_module, "_service_data_manager", lambda: manager)
    service = VideoOrganizerService(on_log=lambda _message: None)

    assert Path(paths.database).is_file()
    assert manager.inspect().state is DataDirectoryState.ACTIVE
    locator = json.loads(Path(manager.locator_path).read_text(encoding="utf-8"))
    assert locator["database_initialized"] is True

    os.remove(paths.database)
    assert manager.inspect().state is DataDirectoryState.INVALID
    assert "缺少数据库" in manager.inspect().message


def test_nonempty_unrecognized_directory_is_not_activated_as_empty(tmp_path: Path):
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "unrelated.txt").write_text("keep", encoding="utf-8")
    manager = DataDirectoryManager(application_directory=str(tmp_path / "app"))

    with pytest.raises(DataDirectoryConflictError, match="非空"):
        manager.activate_empty(str(selected))


def test_locator_falls_back_when_application_directory_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.data_directory as data_directory_module

    app = tmp_path / "app"
    fallback = tmp_path / "user-config" / "locator.json"
    manager = DataDirectoryManager(application_directory=str(app))
    manager.fallback_locator_path = str(fallback)
    original_write = data_directory_module.write_json_atomic

    def fail_primary(path, payload):
        if os.path.abspath(path) == os.path.abspath(manager.locator_path):
            raise OSError("read-only application directory")
        original_write(path, payload)

    monkeypatch.setattr(data_directory_module, "write_json_atomic", fail_primary)

    manager.write_locator(str(tmp_path / "selected"))

    assert fallback.exists()
    assert manager.read_locator() == str((tmp_path / "selected").resolve())


def test_default_directory_permission_failure_can_be_routed_to_alternate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import video_organizer_app as app_module

    from PySide6.QtWidgets import QApplication

    qt_app = QApplication.instance() or QApplication([])
    app_dir = tmp_path / "app"
    alternate = tmp_path / "alternate"
    manager = DataDirectoryManager(application_directory=str(app_dir))
    original_ensure = manager.ensure_writable

    def ensure_writable(root, *, create):
        if os.path.abspath(root) == os.path.abspath(manager.default_root):
            raise DataDirectoryError("默认目录只读")
        return original_ensure(root, create=create)

    monkeypatch.setattr(manager, "ensure_writable", ensure_writable)
    monkeypatch.setattr(app_module, "DataDirectoryManager", lambda: manager)
    monkeypatch.setattr(
        app_module.QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(alternate),
    )
    monkeypatch.setattr(app_module.QMessageBox, "warning", lambda *_args: None)

    assert app_module._prepare_data_directory() is True
    assert manager.read_locator() == str(alternate.resolve())
    qt_app.processEvents()


def test_startup_legacy_and_existing_default_can_open_default_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import video_organizer_app as app_module
    from PySide6.QtWidgets import QApplication, QMessageBox

    qt_app = QApplication.instance() or QApplication([])
    source = tmp_path / "app"
    source.mkdir()
    (source / "video_organizer.db").write_bytes(b"legacy")
    default_root = source / "data"
    default_root.mkdir()
    _make_database(default_root / "video_organizer.db")
    manager = DataDirectoryManager(application_directory=str(source))

    monkeypatch.setattr(app_module, "DataDirectoryManager", lambda: manager)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )

    assert app_module._prepare_data_directory() is True
    assert manager.read_locator() == str(default_root.resolve())
    assert (source / "video_organizer.db").exists()
    qt_app.processEvents()


def test_legacy_migration_uses_alternate_when_default_is_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import video_organizer_app as app_module
    from PySide6.QtWidgets import QApplication, QMessageBox

    qt_app = QApplication.instance() or QApplication([])
    source = tmp_path / "app"
    source.mkdir()
    _make_database(source / "video_organizer.db")
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "tag_config.json").write_text("{}", encoding="utf-8")
    (source / ".thumbnails").mkdir()
    (source / ".thumbnails" / "thumb.webp").write_bytes(b"thumbnail")
    alternate = tmp_path / "external" / "data"
    manager = DataDirectoryManager(application_directory=str(source))
    original_ensure = manager.ensure_writable

    def ensure_writable(root, *, create):
        if os.path.abspath(root) == os.path.abspath(manager.default_root):
            raise DataDirectoryError("默认目录只读")
        return original_ensure(root, create=create)

    monkeypatch.setattr(manager, "ensure_writable", ensure_writable)
    monkeypatch.setattr(app_module, "DataDirectoryManager", lambda: manager)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    monkeypatch.setattr(app_module.QMessageBox, "warning", lambda *_args: None)
    monkeypatch.setattr(
        app_module.QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(alternate),
    )

    assert app_module._prepare_data_directory() is True
    assert Path(alternate, "video_organizer.db").exists()
    assert manager.read_locator() == str(alternate.resolve())
    qt_app.processEvents()


def test_tag_import_uses_service_data_directory_config_path(tmp_path: Path):
    from core.tag_import_service import TagImportService

    class FakeAI:
        tag_config = {
            "tag_groups": [{"id": "mood", "tags": []}],
        }

    target = tmp_path / "selected" / "tag_config.json"
    importer = TagImportService(FakeAI(), tag_config_path=str(target))

    ok, error = importer.persist_classified_tags({"mood": ["纪实"]})

    assert ok is True
    assert error == ""
    assert json.loads(target.read_text(encoding="utf-8"))["tag_groups"][0]["tags"] == ["纪实"]


def test_invalid_database_in_selected_directory_is_rejected(tmp_path: Path):
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "video_organizer.db").write_bytes(b"broken")
    manager = DataDirectoryManager(application_directory=str(tmp_path / "app"))
    manager.write_locator(str(selected))

    inspection = manager.inspect()

    assert inspection.state is DataDirectoryState.INVALID
    with pytest.raises(DataDirectoryError):
        manager.resolve_active_paths()
