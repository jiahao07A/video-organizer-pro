from __future__ import annotations

import time
import threading
from pathlib import Path
from types import SimpleNamespace

from core.media_watch import (
    _EventHandler,
    MediaDirectoryMonitor,
    effective_directory_options,
    normalize_watch_config,
    should_start_with_windows,
)
from core.video_organizer_service import DatabaseManager


def _config(root: Path, **kwargs):
    return normalize_watch_config({"directories": [{"path": str(root), **kwargs}]})


def test_global_defaults_and_deepest_directory_override(tmp_path):
    root = tmp_path / "media"
    child = root / "child"
    config = normalize_watch_config({
        "recursive": True,
        "auto_analyze": True,
        "directories": [
            {"path": str(root)},
            {"path": str(child), "recursive": False, "auto_analyze": False},
        ],
    })

    assert effective_directory_options(config, str(root / "top.mp4"))["recursive"] is True
    nested = effective_directory_options(config, str(child / "clip.mp4"))
    assert nested["recursive"] is False
    assert nested["auto_analyze"] is False
    assert effective_directory_options(config, str(child / "deep" / "clip.mp4"))["recursive"] is False


def test_disabled_nested_directory_masks_parent_watch(tmp_path):
    root = tmp_path / "media"
    child = root / "paused"
    config = normalize_watch_config({"directories": [
        {"path": str(root), "enabled": True},
        {"path": str(child), "enabled": False},
    ]})

    assert effective_directory_options(config, str(root / "top.mp4")) is not None
    assert effective_directory_options(config, str(child / "clip.mp4")) is None


def test_windows_startup_requires_a_user_enabled_watch_directory(tmp_path):
    root = tmp_path / "media"
    assert not should_start_with_windows({"start_with_windows": True, "directories": []})
    assert not should_start_with_windows({
        "start_with_windows": True,
        "directories": [{"path": str(root), "enabled": False}],
    })
    assert not should_start_with_windows({
        "start_with_windows": False,
        "directories": [{"path": str(root), "enabled": True}],
    })
    # 离线但仍启用的目录需要保留自启，登录后才能继续周期补扫。
    assert should_start_with_windows({
        "start_with_windows": True,
        "directories": [{"path": str(root), "enabled": True}],
    })


def test_monitor_start_pause_resume_and_stop(tmp_path):
    root = tmp_path / "media"
    root.mkdir()

    class Database:
        @staticmethod
        def reconcile_monitored_paths(*_args, **_kwargs):
            pass

        @staticmethod
        def get_video_statuses_by_paths(_paths):
            return []

    class Service:
        db = Database()

    monitor = MediaDirectoryMonitor(Service(), _config(root))
    assert monitor.start()
    assert monitor.running
    monitor.pause()
    assert not monitor.running
    monitor.reconfigure(_config(root))
    assert monitor.running
    monitor.stop(shutdown_analysis=True)
    assert not monitor.running


def test_monitor_keeps_rescanning_when_configured_roots_start_offline(tmp_path):
    root = tmp_path / "offline"

    class Database:
        reconciliations = 0
        @staticmethod
        def reconcile_monitored_paths(*_args, **_kwargs):
            assert root.is_dir()
            Database.reconciliations += 1

        @staticmethod
        def get_video_statuses_by_paths(_paths):
            return []

    class Service:
        db = Database()

    monitor = MediaDirectoryMonitor(Service(), _config(root))
    try:
        assert monitor.start()
        assert monitor.running
        root.mkdir()
        monitor.scan_now()
    finally:
        monitor.stop(shutdown_analysis=True)
    assert not monitor.running
    assert Database.reconciliations >= 1


def test_missing_root_scan_is_incomplete_and_does_not_reconcile(tmp_path):
    root = tmp_path / "offline"

    class Database:
        reconciliations = 0
        def reconcile_monitored_paths(self, *_args, **_kwargs):
            self.reconciliations += 1

    class Service:
        def __init__(self):
            self.db = Database()

    service = Service()
    monitor = MediaDirectoryMonitor(service, _config(root))
    try:
        monitor.scan_now()
        assert service.db.reconciliations == 0
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)


def test_auto_analysis_uses_a_deduplicated_serial_queue(tmp_path):
    class Database:
        @staticmethod
        def get_video_by_path(_path):
            return {"status": "analyzed"}

    class Service:
        def __init__(self):
            self.db = Database()
            self.calls = []
            self.active = 0
            self.maximum_active = 0
            self.completed = threading.Event()

        def run_analysis(self, path, **_kwargs):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.calls.append(path)
            time.sleep(0.01)
            self.active -= 1
            if len(self.calls) == 3:
                self.completed.set()

    service = Service()
    monitor = MediaDirectoryMonitor(service, {})
    try:
        for path in ("a.mp4", "b.mp4", "c.mp4", "b.mp4"):
            monitor._queue_analysis(path)
        assert service.completed.wait(3)
        deadline = time.monotonic() + 3
        while monitor.analysis_running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.calls == ["a.mp4", "b.mp4", "c.mp4"]
        assert service.maximum_active == 1
    finally:
        monitor.stop(shutdown_analysis=True)


def test_scan_respects_recursive_and_nested_overrides(tmp_path):
    root = tmp_path / "media"
    child = root / "child"
    deep = child / "deep"
    deep.mkdir(parents=True)
    top_file = root / "top.mp4"
    child_file = child / "child.mp4"
    deep_file = deep / "deep.mp4"
    for path in (top_file, child_file, deep_file):
        path.write_bytes(b"test")
    config = normalize_watch_config({"directories": [
        {"path": str(root)},
        {"path": str(child), "recursive": False},
    ]})
    class Service:
        class Database:
            @staticmethod
            def get_video_by_path(_path):
                return None
        db = Database()

    monitor = MediaDirectoryMonitor(Service(), config)
    try:
        complete, found = monitor._scan_root(config["directories"][0])
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)

    assert complete
    assert set(found) == {str(top_file.resolve()), str(child_file.resolve())}


def test_recursive_false_nested_override_ignores_deep_file_events(tmp_path):
    root = tmp_path / "media"
    child = root / "child"
    deep = child / "deep"
    deep.mkdir(parents=True)
    direct_file = child / "direct.mp4"
    deep_file = deep / "deep.mp4"
    direct_file.write_bytes(b"direct")
    deep_file.write_bytes(b"deep")
    config = normalize_watch_config({"directories": [
        {"path": str(root)},
        {"path": str(child), "recursive": False},
    ]})
    class Service:
        class Database:
            @staticmethod
            def get_video_by_path(_path):
                return None
        db = Database()

    monitor = MediaDirectoryMonitor(Service(), config)
    try:
        monitor.notify_path(str(direct_file))
        monitor.notify_path(str(deep_file))
        assert [row["path"] for row in monitor.statuses()] == [str(direct_file.resolve())]
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)


def test_only_complete_scan_reconciles_missing_and_reappearance(tmp_path):
    db = DatabaseManager(str(tmp_path / "catalog.db"))
    root = tmp_path / "media"
    root.mkdir()
    old = root / "old.mp4"
    old.write_bytes(b"video")
    path = str(old.resolve())
    db.insert_video_if_absent({"path": path, "filename": old.name, "status": "analyzed"})

    db.reconcile_monitored_paths(str(root), [])
    assert db.get_video_by_path(path)["is_missing"] == 1
    db.reconcile_monitored_paths(str(root), [], recursive=True)
    assert db.get_video_by_path(path)["is_missing"] == 1
    db.reconcile_monitored_paths(str(root), [path])
    row = db.get_video_by_path(path)
    assert row["is_missing"] == 0
    assert row["status"] == "analyzed"


def test_stable_video_is_registered_once(tmp_path, monkeypatch):
    from core import media_watch

    root = tmp_path / "media"
    root.mkdir()
    path = root / "clip.mp4"
    path.write_bytes(b"stable video bytes")
    db = DatabaseManager(str(tmp_path / "catalog.db"))

    class Processor:
        @staticmethod
        def get_file_hash(_path):
            return "unique-hash"

    class Service:
        def __init__(self):
            self.db = db
            self.processor = Processor()
            self.registered = 0

        @staticmethod
        def invalidate_video_memory_cache():
            pass

    service = Service()
    monitor = MediaDirectoryMonitor(service, _config(root, auto_analyze=False))
    try:
        monitor.notify_path(str(path))
        key = media_watch._path_key(str(path))
        stat = path.stat()
        monitor._pending[key] = (time.monotonic() - 31, (stat.st_size, stat.st_mtime_ns))
        monkeypatch.setattr(media_watch, "STABILITY_SECONDS", 0)
        monitor._process_stable_files(time.monotonic())
        monitor.notify_path(str(path))
        monitor._process_video(str(path))
        rows = db.execute_query("SELECT path,status FROM videos")
        assert len(rows) == 1
        assert rows[0]["path"] == str(path.resolve())
        assert rows[0]["status"] == "pending"
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)


def test_unique_missing_hash_creates_duplicate_without_inheriting_analysis(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    old = root / "old.mp4"
    moved = root / "moved.mp4"
    old.write_bytes(b"same content")
    moved.write_bytes(b"same content")
    db = DatabaseManager(str(tmp_path / "catalog.db"))
    old_path = str(old.resolve())
    new_path = str(moved.resolve())
    db.insert_video_if_absent({
        "path": old_path, "filename": old.name, "file_hash": "content-hash",
        "status": "analyzed", "summary": "existing analysis",
    })
    db.reconcile_monitored_paths(str(root), [new_path])

    class Processor:
        @staticmethod
        def get_file_hash(_path):
            return "content-hash"

    class Service:
        processor = Processor()
        def __init__(self):
            self.db = db
            self.invalidations = 0

        def invalidate_video_memory_cache(self):
            self.invalidations += 1

    service = Service()
    monitor = MediaDirectoryMonitor(service, _config(root, auto_analyze=False))
    try:
        monitor._process_video(new_path)
        row = db.get_video_by_path(new_path)
        assert row["status"] == "pending"
        assert not row["summary"]
        assert row['duplicate_of'] == db.get_video_by_path(old_path)['id']
        assert db.get_video_by_path(old_path)['summary'] == 'existing analysis'
        assert service.invalidations == 1
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)


def test_reappearing_video_clears_missing_and_invalidates_cache(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    path = root / "clip.mp4"
    path.write_bytes(b"video")
    db = DatabaseManager(str(tmp_path / "catalog.db"))
    stored_path = str(path.resolve()).replace("/", "\\")
    db.insert_video_if_absent({"path": stored_path, "filename": path.name, "status": "analyzed"})
    db.set_video_missing(stored_path, True)

    class Service:
        def __init__(self):
            self.db = db
            self.invalidations = 0

        def invalidate_video_memory_cache(self):
            self.invalidations += 1

    service = Service()
    monitor = MediaDirectoryMonitor(service, _config(root, auto_analyze=False))
    try:
        monitor.notify_path(str(path))
        assert db.get_video_by_path(str(path))["is_missing"] == 0
        assert service.invalidations == 1
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)


def test_complete_scan_invalidates_cache_when_missing_state_changes(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    path = root / "clip.mp4"
    db = DatabaseManager(str(tmp_path / "catalog.db"))
    stored_path = str(path.resolve())
    db.insert_video_if_absent({"path": stored_path, "filename": path.name, "status": "analyzed"})

    class Service:
        def __init__(self):
            self.db = db
            self.invalidations = 0

        def invalidate_video_memory_cache(self):
            self.invalidations += 1

    service = Service()
    monitor = MediaDirectoryMonitor(service, _config(root))
    try:
        monitor.scan_now()
        assert db.get_video_by_path(stored_path)["is_missing"] == 1
        assert service.invalidations == 1

        path.write_bytes(b"video")
        monitor.scan_now()
        assert db.get_video_by_path(stored_path)["is_missing"] == 0
        assert service.invalidations == 2
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)


def test_external_move_keeps_old_identity_and_marks_new_path_duplicate(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    old = root / "old.mp4"
    moved = root / "moved.mp4"
    old.write_bytes(b"same content")
    db = DatabaseManager(str(tmp_path / "catalog.db"))
    old_path = str(old.resolve())
    new_path = str(moved.resolve())
    db.insert_video_if_absent({
        "path": old_path, "filename": old.name, "file_hash": "content-hash",
        "status": "analyzed", "summary": "existing analysis",
    })

    class Processor:
        @staticmethod
        def get_file_hash(_path):
            return "content-hash"

    class Service:
        processor = Processor()
        def __init__(self):
            self.db = db
            self.invalidations = 0

        def invalidate_video_memory_cache(self):
            self.invalidations += 1

    service = Service()
    monitor = MediaDirectoryMonitor(service, _config(root, auto_analyze=False))
    old.rename(moved)
    try:
        handler = _EventHandler(monitor.notify_path, monitor.remember_move_source)
        handler.on_moved(SimpleNamespace(
            is_directory=False, src_path=old_path, dest_path=new_path,
        ))
        assert db.get_video_by_path(old_path)["is_missing"] == 0
        monitor._process_video(new_path)
        row = db.get_video_by_path(new_path)
        assert row["status"] == "pending"
        assert not row["summary"]
        assert row['duplicate_of'] == db.get_video_by_path(old_path)['id']
        assert db.get_video_by_path(old_path)['summary'] == 'existing analysis'
        assert service.invalidations == 1
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)


def test_path_lookup_is_case_and_separator_insensitive(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    original = root / "clip.mp4"
    original.write_bytes(b"video")
    db = DatabaseManager(str(tmp_path / "catalog.db"))
    stored_path = str(original.resolve()).replace("/", "\\")
    db.insert_video_if_absent({"path": stored_path, "filename": original.name, "status": "pending"})

    lookup_path = str(original.resolve()).upper()
    assert db.get_video_by_path(lookup_path)["path"] == stored_path
    assert db.is_path_cataloged(lookup_path)


def test_matching_copy_does_not_merge_while_original_is_available(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    original = root / "original.mp4"
    copy = root / "copy.mp4"
    original.write_bytes(b"same content")
    copy.write_bytes(b"same content")
    db = DatabaseManager(str(tmp_path / "catalog.db"))
    old_path = str(original.resolve())
    copy_path = str(copy.resolve())
    db.insert_video_if_absent({
        "path": old_path, "filename": original.name, "file_hash": "content-hash",
        "status": "analyzed", "summary": "keep original",
    })

    class Processor:
        @staticmethod
        def get_file_hash(_path):
            return "content-hash"

    class Service:
        processor = Processor()
        def __init__(self):
            self.db = db
        @staticmethod
        def invalidate_video_memory_cache():
            pass

    monitor = MediaDirectoryMonitor(Service(), _config(root, auto_analyze=False))
    try:
        monitor._process_video(copy_path)
        assert db.get_video_by_path(old_path)["summary"] == "keep original"
        assert db.get_video_by_path(copy_path)["status"] == "pending"
    finally:
        monitor._analysis_pool.shutdown(wait=False, cancel_futures=True)
