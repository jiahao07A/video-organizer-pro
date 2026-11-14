"""Windows 编辑器产生的 UTF-8 BOM 配置应能原样备份、恢复和搬迁。"""
from __future__ import annotations

import codecs
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from core.data_directory import DataDirectoryError, DataDirectoryManager
from core.storage_backup import StorageBackupError, StorageBackupManager
from core.video_organizer_service import SettingsManager, VideoOrganizerService


def _layout(root: Path, *, bom: bool):
    root.mkdir()
    database = root / "video_organizer.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, path TEXT, thumbnail TEXT)")
        connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO videos(path, thumbnail) VALUES ('synthetic.mp4', NULL)")
    encoding = "utf-8-sig" if bom else "utf-8"
    (root / "settings.json").write_text('{"marker": "原始设置"}', encoding=encoding)
    (root / "tag_config.json").write_text('{"version": "6.0", "tag_groups": []}', encoding=encoding)
    return StorageBackupManager(
        database_path=str(database),
        settings_path=str(root / "settings.json"),
        tag_config_path=str(root / "tag_config.json"),
        backup_dir=str(root / "backups"),
    )


@pytest.mark.parametrize("bom", [False, True])
def test_backup_restore_preserves_config_bytes_and_settings_readback(tmp_path, bom):
    root = tmp_path / "source"
    manager = _layout(root, bom=bom)
    original = {name: (root / name).read_bytes() for name in ("settings.json", "tag_config.json")}
    assert original["settings.json"].startswith(codecs.BOM_UTF8) is bom

    backup = Path(manager.create_backup())
    manifest = manager.validate_backup(str(backup))
    for key, name in (("settings", "settings.json"), ("tag_config", "tag_config.json")):
        assert (backup / name).read_bytes() == original[name]
        assert manifest["files"][key]["sha256"] == hashlib.sha256(original[name]).hexdigest()
    (root / "settings.json").write_text('{"marker": "修改后"}', encoding="utf-8")
    (root / "tag_config.json").write_text('{"version": "changed"}', encoding="utf-8")
    with sqlite3.connect(manager.database_path) as connection:
        connection.execute("UPDATE videos SET path='changed.mp4'")

    manager.restore(str(backup))

    assert {name: (root / name).read_bytes() for name in original} == original
    assert SettingsManager.load_settings(settings_path=manager.settings_path)["marker"] == "原始设置"
    with sqlite3.connect(manager.database_path) as connection:
        assert connection.execute("SELECT path FROM videos").fetchone() == ("synthetic.mp4",)


def test_bom_settings_service_restarts_after_backup_restore(tmp_path):
    database = tmp_path / "video_organizer.db"
    results = tmp_path / "results.json"
    service = VideoOrganizerService(db_path=str(database), results_json=str(results))
    settings_path = Path(service._settings_path)
    settings = SettingsManager.deep_copy_defaults()
    settings["review_marker"] = "BOM启动值"
    settings_path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8-sig")
    original = settings_path.read_bytes()
    backup_manager = service._storage_backup_manager()
    backup = backup_manager.create_backup()
    settings_path.write_text('{"review_marker": "修改后"}', encoding="utf-8")
    backup_manager.restore(backup)
    assert settings_path.read_bytes() == original

    restarted = VideoOrganizerService(db_path=str(database), results_json=str(results))
    assert restarted.settings["review_marker"] == "BOM启动值"


@pytest.mark.parametrize("bom", [False, True])
def test_migration_validates_bom_and_preserves_source_and_target_bytes(tmp_path, bom):
    source = tmp_path / "source"
    _layout(source, bom=bom)
    original = {name: (source / name).read_bytes() for name in ("settings.json", "tag_config.json")}
    directory = DataDirectoryManager(application_directory=str(tmp_path / "app"))

    target = directory.migrate_root(str(source), str(tmp_path / "target"))

    assert directory.read_locator() == target.root
    for name, content in original.items():
        assert (source / name).read_bytes() == content
        assert (Path(target.root) / name).read_bytes() == content
    assert SettingsManager.load_settings(settings_path=target.settings)["marker"] == "原始设置"
    snapshot_dirs = list(Path(target.backups).glob("backup_*"))
    assert len(snapshot_dirs) == 1
    assert (snapshot_dirs[0] / "settings.json").read_bytes() == original["settings.json"]


@pytest.mark.parametrize("name", ["settings.json", "tag_config.json"])
@pytest.mark.parametrize("content", ['{"broken":', "[]"])
def test_invalid_bom_configuration_still_blocks_backup_and_migration(tmp_path, name, content):
    source = tmp_path / "source"
    manager = _layout(source, bom=True)
    invalid_path = source / name
    invalid_path.write_text(content, encoding="utf-8-sig")
    original = invalid_path.read_bytes()
    with pytest.raises(StorageBackupError, match="JSON"):
        manager.create_backup()
    assert invalid_path.read_bytes() == original
    directory = DataDirectoryManager(application_directory=str(tmp_path / "app"))
    with pytest.raises((DataDirectoryError, StorageBackupError), match="JSON"):
        directory.migrate_root(str(source), str(tmp_path / "target"))
    assert invalid_path.read_bytes() == original
    assert directory.read_locator() is None
    assert not (tmp_path / "target").exists()
