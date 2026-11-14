"""真实扫描、完整指纹、SQLite、首帧解码与进度的隔离集成回归。"""
import hashlib
from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pytest

from core.video_organizer_service import SettingsManager, VideoOrganizerService


@pytest.fixture
def service(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setattr('core.video_organizer_service.TAG_CONFIG_FILE', str(data / 'tags.json'))
    monkeypatch.setattr('core.video_organizer_service.get_settings_file_path', lambda: str(data / 'settings.json'))
    settings = SettingsManager.deep_copy_defaults()
    settings['processing'].update(extract_workers=512, target_size=64)
    instance = VideoOrganizerService(
        settings=settings, on_log=lambda _: None,
        db_path=str(data / 'catalog.db'), results_json=str(data / 'results.json'),
        results_csv=str(data / 'results.csv'),
    )
    assert Path(instance._thumbnail_dir).is_relative_to(tmp_path)
    return instance


def make_video(tmp_path, name, color=24):
    path = tmp_path / name
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (160, 96))
    assert writer.isOpened(), '测试环境必须支持真实合成 MP4 编码'
    try:
        for index in range(4):
            writer.write(np.full((96, 160, 3), (color + index * 5) % 255, dtype=np.uint8))
    finally:
        writer.release()
    assert path.stat().st_size > 0
    return path


def assert_progress(events):
    assert events
    percentages = [event['percent'] for event in events]
    assert percentages == sorted(percentages)
    assert all(0 <= value <= 1000 for value in percentages)
    assert all(event['percent'] < 1000 for event in events if event['phase'] != 'complete')
    assert events[-1]['phase'] == 'complete'
    assert events[-1]['percent'] == 1000
    elapsed = [event['elapsed_seconds'] for event in events]
    assert elapsed == sorted(elapsed)
    assert all(event['completed'] >= 0 and event['total'] >= 0 for event in events)


def decode_thumbnail(row, tmp_path):
    assert row['thumbnail']
    path = Path(row['thumbnail_path'])
    assert path.is_relative_to(tmp_path)
    image = cv2.imread(str(path))
    assert image is not None, '数据库缩略图必须真实可解码'
    assert max(image.shape[:2]) <= 64
    return path


def test_real_scan_progress_committed_counts_duplicates_and_decodable_thumbnails(service, tmp_path):
    first = make_video(tmp_path, '01-original.mp4')
    copy = tmp_path / '02-copy.mp4'
    shutil.copyfile(first, copy)
    other = make_video(tmp_path, '03-other.mp4', color=190)
    service.replace_work_scope([str(first), str(copy), str(other)], scan=False)
    caller = threading.get_ident()
    events = []

    def progress(snapshot):
        assert threading.get_ident() == caller
        events.append(snapshot)
        if snapshot['phase'] == 'register':
            # 独立连接可见，确保不是未提交事务上的进度假象。
            with sqlite3.connect(service.db.db_path) as connection:
                actual = connection.execute('SELECT COUNT(*) FROM videos').fetchone()[0]
            assert actual == snapshot['completed']

    registered = service.scan_and_register_work_scope(progress_callback=progress)
    assert set(registered) == {str(first), str(copy), str(other)}
    assert_progress(events)
    assert {event['phase'] for event in events} == {
        'discovery', 'history', 'fingerprint', 'register', 'thumbnail', 'complete',
    }
    rows = service.db.get_all_videos()
    assert len(rows) == 3
    originals = [row for row in rows if not row.get('duplicate_of')]
    duplicates = [row for row in rows if row.get('duplicate_of')]
    assert len(originals) == 2 and len(duplicates) == 1
    owner = next(row for row in rows if row['path'] == str(first))
    assert duplicates[0]['duplicate_of'] == owner['id']
    assert not duplicates[0]['thumbnail']
    thumbnails = [decode_thumbnail(row, tmp_path) for row in originals]
    assert len(set(thumbnails)) == 2
    for row in rows:
        assert row['file_hash'] == hashlib.md5(Path(row['path']).read_bytes()).hexdigest()
        assert row['fingerprint_state'] == 'verified'
    final = events[-1]
    assert (final['files_found'], final['new_count'], final['duplicate_count'], final['error_count']) == (3, 3, 1, 0)
    assert service._last_scan_result == final


def test_damaged_video_counts_real_decode_error_and_other_video_still_completes(service, tmp_path):
    damaged = tmp_path / 'broken.mp4'
    damaged.write_bytes(b'not a media container')
    good = make_video(tmp_path, 'good.mp4')
    service.replace_work_scope([str(damaged), str(good)], scan=False)
    events = []
    registered = service.scan_and_register_work_scope(progress_callback=events.append)
    assert set(registered) == {str(damaged), str(good)}
    assert_progress(events)
    assert events[-1]['error_count'] == 1
    assert events[-1]['new_count'] == 2
    assert not service.db.get_video_by_path(str(damaged))['thumbnail']
    decode_thumbnail(service.db.get_video_by_path(str(good)), tmp_path)
    assert len(service.db.get_all_videos()) == 2


def test_empty_scope_completes_without_database_or_thumbnail_work(service, tmp_path):
    events = []
    assert service.scan_and_register_work_scope(progress_callback=events.append) == []
    assert_progress(events)
    assert [event['phase'] for event in events] == ['discovery', 'complete']
    assert events[-1]['files_found'] == events[-1]['new_count'] == events[-1]['error_count'] == 0
    assert service.db.get_all_videos() == []
    assert not list(Path(service._thumbnail_dir).glob('*'))


def test_rescan_changed_existing_content_invalidates_complete_memory_cache(service, tmp_path):
    path = make_video(tmp_path, 'changing.mp4')
    service.replace_work_scope([str(path)], scan=False)
    assert service.scan_and_register_work_scope() == [str(path)]
    original = service.db.get_video_by_path(str(path))
    service.db.execute_non_query("UPDATE videos SET summary='obsolete analysis',status='analyzed' WHERE id=?", (original['id'],))
    cached = service.get_all_videos()[0]
    assert cached['summary'] == 'obsolete analysis'
    assert service._memory_cache_complete
    make_video(tmp_path, path.name, color=200)
    assert hashlib.md5(path.read_bytes()).hexdigest() != original['file_hash']
    events = []
    assert service.scan_and_register_work_scope(progress_callback=events.append) == []
    actual = service.get_all_videos()[0]
    persisted = service.db.get_video_by_path(str(path))
    assert actual['id'] == original['id']
    assert actual['content_version'] == original['content_version'] + 1
    assert actual['file_hash'] == persisted['file_hash'] == hashlib.md5(path.read_bytes()).hexdigest()
    assert actual['summary'] is None and actual['status'] == 'pending'
    decode_thumbnail(actual, tmp_path)
    assert events[-1]['new_count'] == 0
    assert_progress(events)


@pytest.mark.parametrize('when', ['before', 'after'])
def test_changed_file_around_thumbnail_generation_is_not_bound_and_is_reported(
    service, tmp_path, monkeypatch, when,
):
    changing = make_video(tmp_path, 'changing.mp4')
    good = make_video(tmp_path, 'good.mp4', color=140)
    service.replace_work_scope([str(changing), str(good)], scan=False)
    original_extract = service.processor.extract_thumbnail
    calls, produced = [], []
    events = []

    def mutate():
        with changing.open('ab') as stream:
            stream.write(b'changed after registration')

    def extract(path, **kwargs):
        calls.append(path)
        thumbnail = original_extract(path, **kwargs)
        if path == str(changing):
            assert thumbnail
            produced.append(thumbnail)
            if when == 'after':
                mutate()
        return thumbnail

    def progress(snapshot):
        events.append(snapshot)

    original_register = service.media_registry.register_many

    def register(*args, on_batch=None, **kwargs):
        def committed(items):
            if when == 'before' and any(rec['path'] == str(changing) for rec, _ in items):
                mutate()
            on_batch(items)
        return original_register(*args, on_batch=committed, **kwargs)

    monkeypatch.setattr(service.processor, 'extract_thumbnail', extract)
    monkeypatch.setattr(service.media_registry, 'register_many', register)
    assert len(service.scan_and_register_work_scope(progress_callback=progress)) == 2
    assert_progress(events)
    assert events[-1]['error_count'] == 1
    row = service.db.get_video_by_path(str(changing))
    assert not row['thumbnail'], '变化后的素材不得绑定旧内容版本缩略图'
    assert row['file_size'] != changing.stat().st_size
    decode_thumbnail(service.db.get_video_by_path(str(good)), tmp_path)
    if when == 'before':
        assert str(changing) not in calls and produced == []
    else:
        assert str(changing) in calls
        assert cv2.imread(produced[0]) is not None  # 有效产物也必须因素材变化被拒绝绑定。


def test_change_after_thumbnail_result_validation_before_flush_is_rejected(service, tmp_path, monkeypatch):
    path = make_video(tmp_path, 'late-change.mp4')
    service.replace_work_scope([str(path)], scan=False)
    original_connection = service.db._managed_connection
    changed = []
    armed = []

    @contextmanager
    def connection():
        # 缩略图阶段开始后的第一次连接用于flush，位于结果stat之后、
        # 获取db.lock之后。保留真实SQLite连接，只在写事务入口改变真实文件。
        if armed and not changed:
            with path.open('ab') as stream:
                stream.write(b'changed while waiting to flush')
            changed.append(True)
        with original_connection() as conn:
            yield conn

    monkeypatch.setattr(service.db, '_managed_connection', connection)
    events = []

    def progress(snapshot):
        events.append(snapshot)
        if snapshot['phase'] == 'thumbnail' and snapshot['completed'] == 0:
            armed.append(True)

    assert service.scan_and_register_work_scope(progress_callback=progress) == [str(path)]
    assert changed
    assert_progress(events)
    row = service.db.get_video_by_path(str(path))
    assert not row['thumbnail']
    assert events[-1]['error_count'] == 1


def test_external_sqlite_write_lock_file_change_is_rechecked_before_thumbnail_binding(
    service, tmp_path, monkeypatch,
):
    path = make_video(tmp_path, 'external-lock.mp4')
    service.replace_work_scope([str(path)], scan=False)
    original_connection = service.db._managed_connection
    external_locked, flush_waiting = threading.Event(), threading.Event()
    traced = []
    # 此连接不使用DatabaseManager的Python锁，代表其它进程的SQLite写事务。
    external = sqlite3.connect(service.db.db_path, check_same_thread=False, timeout=5)

    def trace(statement):
        normalized = ' '.join(statement.upper().split())
        if external_locked.is_set() and (
            normalized == 'BEGIN IMMEDIATE'
            or normalized.startswith('UPDATE VIDEOS SET THUMBNAIL=')
        ):
            traced.append(normalized)
            # SQLite trace在执行语句/等待写锁前通知。新实现停于BEGIN IMMEDIATE；
            # 无显式BEGIN的旧实现停于UPDATE，均为真实SQL和真实外部写锁。
            flush_waiting.set()

    @contextmanager
    def connection():
        with original_connection() as conn:
            if external_locked.is_set():
                conn.set_trace_callback(trace)
            yield conn

    events = []

    def progress(snapshot):
        events.append(snapshot)
        if snapshot['phase'] == 'thumbnail' and snapshot['completed'] == 0:
            external.execute('BEGIN IMMEDIATE')
            external_locked.set()

    monkeypatch.setattr(service.db, '_managed_connection', connection)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.scan_and_register_work_scope, progress_callback=progress)
            try:
                assert external_locked.wait(4), '必须先完成真实登记再持有外部写锁'
                assert flush_waiting.wait(4), '必须观察到实际flush SQL准备获取SQLite写锁'
                assert external.in_transaction and not future.done()
                with path.open('ab') as stream:
                    stream.write(b'changed while sqlite writer lock was held')
            finally:
                external.commit()
            assert future.result(timeout=5) == [str(path)]
        assert traced
        assert_progress(events)
        row = service.db.get_video_by_path(str(path))
        assert not row['thumbnail'], 'SQLite写锁等待期间变化的素材不得绑定旧内容缩略图'
        assert events[-1]['error_count'] == 1
    finally:
        external.close()
