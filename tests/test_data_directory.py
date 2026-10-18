# -*- coding: utf-8 -*-
"""统一数据目录与安全搬迁测试。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

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


def test_locator_is_path_only_and_survives_working_directory_changes(tmp_path: Path):
    app = tmp_path / "app"
    selected = tmp_path / "selected"
    manager = DataDirectoryManager(application_directory=str(app))
    manager.ensure_writable(str(selected), create=True)
    manager.write_locator(str(selected))

    other = tmp_path / "other"
    other.mkdir()
    manager2 = DataDirectoryManager(application_directory=str(app))
    assert manager2.read_locator() == str(selected.resolve())
    assert manager2.inspect().active_root == str(selected.resolve())
    payload = json.loads((app / ".video-organizer-data.json").read_text(encoding="utf-8"))
    assert set(payload) >= {"format", "version", "data_root"}
    assert "settings" not in payload
    assert "api" not in payload


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
