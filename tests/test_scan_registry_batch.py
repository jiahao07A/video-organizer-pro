"""批量登记使用真实文件和 SQLite，覆盖身份、事务隔离与进度可见性。"""
import hashlib
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest

from core.media_registry import MediaRegistry, full_hash
from core.video_organizer_service import DatabaseManager


def make_registry(tmp_path, **kwargs):
    db = DatabaseManager(str(tmp_path / 'catalog.db'))
    return db, MediaRegistry(db, **kwargs)


def make_file(tmp_path, name, content=b'same video'):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def test_batch_preserves_input_order_and_returns_final_duplicate_classification(tmp_path):
    db, registry = make_registry(tmp_path)
    paths = [make_file(tmp_path, f'{i}.mp4') for i in range(4)]
    result = registry.register_many(paths + paths[:1], batch_size=2)
    assert not result['errors']
    rows = [row for row, new in result['items']]
    assert [row['path'] for row in rows] == paths
    assert all(new for _, new in result['items'])
    assert [row['id'] for row in rows] == sorted(row['id'] for row in rows)
    assert rows[0]['duplicate_of'] is None
    assert all(row['duplicate_of'] == rows[0]['id'] for row in rows[1:])
    assert all(row['duplicate_group'] == hashlib.md5(b'same video').hexdigest() for row in rows)
    assert db.execute_query('SELECT COUNT(*) n FROM media_discoveries')[0]['n'] == 4


def test_batch_rescan_preserves_manual_work_and_content_history(tmp_path):
    db, registry = make_registry(tmp_path)
    path = make_file(tmp_path, 'editable.mp4', b'old video')
    before = registry.register(path)
    db.execute_non_query("UPDATE videos SET manual_override=1,summary='人工说明',status='edited' WHERE id=?", (before['id'],))
    same = registry.register_many([path])['items'][0]
    assert same[0]['id'] == before['id'] and not same[1]
    assert same[0]['content_version'] == 1
    with open(path, 'wb') as stream:
        stream.write(b'changed entire video')
    after, new = registry.register_many([path])['items'][0]
    assert not new and after['id'] == before['id']
    assert after['content_version'] == 2
    assert after['summary'] == '人工说明' and after['fingerprint_state'] == 'review'
    history = db.execute_query('SELECT * FROM media_content_versions')
    assert len(history) == 1 and history[0]['file_hash'] == before['file_hash']


def test_batch_backfills_older_record_before_assigning_owner(tmp_path):
    db, registry = make_registry(tmp_path)
    old = make_file(tmp_path, 'history.mp4')
    db.insert_video_if_absent({'path': old, 'filename': 'history.mp4', 'summary': 'existing', 'status': 'analyzed'})
    copy = make_file(tmp_path, 'new.mp4')
    row, _ = registry.register_many([copy])['items'][0]
    history = db.get_video_by_path(old)
    assert row['duplicate_of'] == history['id']
    assert history['summary'] == 'existing'
    assert history['fingerprint_state'] == 'verified'


def test_batch_isolates_missing_changed_and_late_changed_files(tmp_path):
    paths = [make_file(tmp_path, name, b'original') for name in ('ok.mp4', 'during.mp4', 'late.mp4')]
    missing = str(tmp_path / 'missing.mp4')

    def hasher(path):
        digest = full_hash(path)
        if path == paths[1]:
            with open(path, 'ab') as stream:
                stream.write(b'changed')
        return digest

    db, registry = make_registry(tmp_path, hasher=hasher)

    def progress(phase, done, total):
        if phase == 'fingerprint' and done == total:
            with open(paths[2], 'ab') as stream:
                stream.write(b'late change')

    result = registry.register_many(paths + [missing], progress=progress)
    assert [row['path'] for row, _ in result['items']] == paths[:1]
    assert {item['path'] for item in result['errors']} == {paths[1], paths[2], missing}
    assert len(db.get_all_videos()) == 1
    assert any('校验期间' in item['reason'] for item in result['errors'])
    assert any('校验后' in item['reason'] for item in result['errors'])


def test_batch_savepoint_rolls_back_real_sqlite_failure_without_losing_other_items(tmp_path):
    db, registry = make_registry(tmp_path)
    paths = [make_file(tmp_path, name, name.encode()) for name in ('before.mp4', 'reject.mp4', 'after.mp4')]
    # 在插入视频、更新指纹、建立内容组后失败，验证保存点撤销完整单项。
    db.execute_non_query("CREATE TRIGGER reject_discovery BEFORE INSERT ON media_discoveries WHEN (SELECT filename FROM videos WHERE id=NEW.video_id)='reject.mp4' BEGIN SELECT RAISE(ABORT, 'test rejection'); END")
    result = registry.register_many(paths)
    assert [row['path'] for row, _ in result['items']] == [paths[0], paths[2]]
    assert result['errors'] == [{'path': paths[1], 'reason': 'test rejection'}]
    assert len(db.get_all_videos()) == 2
    assert db.execute_query('SELECT COUNT(*) n FROM media_content_groups')[0]['n'] == 2
    assert db.execute_query('SELECT COUNT(*) n FROM media_discoveries')[0]['n'] == 2


def test_failed_existing_content_update_rolls_back_history_and_metadata_reset(tmp_path):
    db, registry = make_registry(tmp_path)
    path = make_file(tmp_path, 'existing.mp4', b'old content')
    original = registry.register(path)
    db.execute_non_query("UPDATE videos SET summary='saved analysis',status='analyzed' WHERE id=?", (original['id'],))
    db.execute_non_query("CREATE TRIGGER reject_change BEFORE INSERT ON media_discoveries BEGIN SELECT RAISE(ABORT, 'reject update'); END")
    with open(path, 'wb') as stream:
        stream.write(b'replacement')
    result = registry.register_many([path])
    assert not result['items'] and len(result['errors']) == 1
    row = db.get_video_by_path(path)
    assert row['file_hash'] == original['file_hash']
    assert row['content_version'] == 1 and row['summary'] == 'saved analysis'
    assert db.execute_query('SELECT COUNT(*) n FROM media_content_versions')[0]['n'] == 0
    assert db.execute_query('SELECT COUNT(*) n FROM media_content_groups')[0]['n'] == 1


@pytest.mark.skipif(os.name != 'nt', reason='Windows 批量等价路径索引')
def test_batch_builds_equivalent_path_index_once_when_database_is_unchanged(tmp_path, monkeypatch):
    _, registry = make_registry(tmp_path)
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(12)]
    calls = []
    original = registry._path_index

    def build_index(connection):
        calls.append(1)
        return original(connection)

    monkeypatch.setattr(registry, '_path_index', build_index)
    assert not registry.register_many(paths, batch_size=2)['errors']
    assert len(calls) == 1


def test_progress_runs_on_caller_and_register_reports_committed_rows(tmp_path):
    db, registry = make_registry(tmp_path)
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(5)]
    caller = threading.get_ident()
    events = []

    def progress(phase, done, total):
        assert threading.get_ident() == caller
        assert total == len(paths)
        events.append((phase, done))
        if phase == 'register':
            with sqlite3.connect(db.db_path) as connection:
                assert connection.execute('SELECT COUNT(*) FROM videos').fetchone()[0] == done

    registry.register_many(paths, batch_size=2, progress=progress)
    for phase in ('fingerprint', 'register'):
        completed = [done for event_phase, done in events if event_phase == phase]
        assert completed == sorted(completed)
        assert completed[0] == 0 and completed[-1] == len(paths)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Unicode 路径等价规则')
def test_batch_unicode_case_and_slash_variants_reuse_identity(tmp_path):
    db, registry = make_registry(tmp_path)
    path = make_file(tmp_path, 'Ä视频.mp4')
    variants = [path, path.replace('Ä', 'ä').replace('\\', '/')]
    result = registry.register_many(variants, batch_size=1)
    assert not result['errors']
    assert len({row['id'] for row, _ in result['items']}) == 1
    assert [new for _, new in result['items']] == [True, False]
    assert len(db.get_all_videos()) == 1


@pytest.mark.skipif(os.name != 'nt', reason='Windows 批次间路径索引刷新')
def test_batch_refreshes_path_identity_after_other_connection_commit(tmp_path):
    db, registry = make_registry(tmp_path)
    first = make_file(tmp_path, 'first.mp4', b'first')
    upper = make_file(tmp_path, 'Ä.mp4', b'second')
    lower = upper.replace('Ä', 'ä')
    other_db = DatabaseManager(db.db_path)
    other_registry = MediaRegistry(other_db)
    inserted = []

    def progress(phase, done, total):
        if phase == 'register' and done == 1:
            inserted.append(other_registry.register(upper))

    result = registry.register_many([first, lower], batch_size=1, progress=progress)
    assert not result['errors']
    assert result['items'][1][0]['id'] == inserted[0]['id']
    assert not result['items'][1][1]
    assert len(db.get_all_videos()) == 2


def test_batch_parallel_hashing_is_bounded_and_order_is_not_completion_order(tmp_path):
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(9)]
    active = peak = 0
    guard = threading.Lock()
    completion = []

    def hasher(path):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02 if path == paths[0] else 0.001)
        digest = full_hash(path)
        with guard:
            active -= 1
            completion.append(path)
        return digest

    _, registry = make_registry(tmp_path, hasher=hasher)
    result = registry.register_many(paths, workers=30)
    assert 1 < peak <= 4
    assert completion[0] != paths[0]
    assert [row['path'] for row, _ in result['items']] == paths


def test_batch_hashes_tail_bytes_and_concurrent_discovery_keeps_one_owner(tmp_path):
    db, registry = make_registry(tmp_path)
    prefix = b'v' * (1024 * 1024 + 9)
    paths = [make_file(tmp_path, f'{i}.mp4', prefix + (b'a' if i < 6 else b'b')) for i in range(7)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        batch = pool.submit(registry.register_many, paths[:5])
        single = pool.submit(registry.register, paths[5])
        assert not batch.result()['errors']
        assert single.result()
    tail = registry.register_many(paths[6:])['items'][0][0]
    rows = db.get_all_videos()
    same = [row for row in rows if row['file_hash'] != tail['file_hash']]
    owners = [row for row in same if row['duplicate_of'] is None]
    assert len(owners) == 1 and len(same) == 6
    assert all(row['duplicate_of'] == owners[0]['id'] for row in same if row['id'] != owners[0]['id'])
    assert tail['duplicate_of'] is None


def test_batch_propagates_fresh_caller_context_to_each_hash_job(tmp_path):
    diagnostic = ContextVar('batch_diagnostic', default='missing')
    observed = []
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(5)]

    def hasher(path):
        observed.append(diagnostic.get())
        diagnostic.set(path)
        return full_hash(path)

    _, registry = make_registry(tmp_path, hasher=hasher)
    token = diagnostic.set('scan-session')
    try:
        result = registry.register_many(paths, workers=1)
        assert not result['errors']
        assert diagnostic.get() == 'scan-session'
    finally:
        diagnostic.reset(token)
    assert observed == ['scan-session'] * len(paths)


def test_single_register_rechecks_after_database_lock_wait_and_preserves_prior_state(tmp_path):
    db, registry = make_registry(tmp_path)
    path = make_file(tmp_path, 'waiting.mp4', b'old content')
    original = registry.register(path)
    db.execute_non_query("UPDATE videos SET summary='human work',manual_override=1,status='edited' WHERE id=?", (original['id'],))
    fingerprint_ready = threading.Event()
    original_prepare = registry._prepare_fingerprint

    def prepare_and_signal(candidate):
        prepared = original_prepare(candidate)
        fingerprint_ready.set()
        return prepared

    registry._prepare_fingerprint = prepare_and_signal
    with ThreadPoolExecutor(max_workers=1) as pool:
        # 主线程锁住真实DB锁，保证子线程哈希成功后尚未进入写事务。
        with db.lock:
            future = pool.submit(registry.register, path, index_existing=False)
            assert fingerprint_ready.wait(4)
            with open(path, 'wb') as stream:
                stream.write(b'replaced while waiting for database')
        with pytest.raises(OSError, match='校验后'):
            future.result(timeout=4)
    row = db.get_video_by_path(path)
    assert row['file_hash'] == original['file_hash']
    assert row['content_version'] == original['content_version']
    assert row['summary'] == 'human work' and row['status'] == 'edited'
    assert row['fingerprint_state'] == 'verified'
    assert row['duplicate_of'] is None
    assert db.execute_query('SELECT COUNT(*) n FROM media_content_groups')[0]['n'] == 1
    assert db.execute_query('SELECT COUNT(*) n FROM media_content_versions')[0]['n'] == 0
    assert db.execute_query('SELECT COUNT(*) n FROM media_discoveries')[0]['n'] == 1


def test_batch_callback_sees_committed_data_without_transaction_or_database_lock(tmp_path):
    db, registry = make_registry(tmp_path)
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(5)]
    snapshots = []
    lock_checks = []

    def callback(items):
        assert not db._connection_local.connection.in_transaction
        # RLock 本线程可重入；必须从另一真实线程证明调用者未持有数据库锁。
        def acquire_from_other_thread():
            acquired = db.lock.acquire(timeout=1)
            lock_checks.append(acquired)
            if acquired:
                db.lock.release()
        thread = threading.Thread(target=acquire_from_other_thread)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive() and lock_checks[-1]
        snapshots.extend(items)
        with sqlite3.connect(db.db_path) as connection:
            assert connection.execute('SELECT COUNT(*) FROM videos').fetchone()[0] == len(snapshots)
            connection.execute("UPDATE videos SET review_state='reviewed' WHERE id=?", (items[0][0]['id'],))
        assert all(row['tags'] == [] and row['library_id'] == row['id'] and new
                   for row, new in items)

    result = registry.register_many(paths, batch_size=2, on_batch=callback)
    assert not result['errors']
    assert len(snapshots) == 5 and lock_checks == [True] * 3
    assert [row['path'] for row, _ in snapshots] == paths
    assert sum(row['review_state'] == 'reviewed' for row, _ in result['items']) == 3


def test_batch_callback_has_committed_snapshot_but_final_return_has_final_classification(tmp_path):
    _, registry = make_registry(tmp_path)
    paths = [make_file(tmp_path, f'{i}.mp4') for i in range(2)]
    snapshots = []
    result = registry.register_many(paths, batch_size=1, on_batch=lambda items: snapshots.extend(items))
    assert snapshots[0][0]['duplicate_group'] == ''
    assert result['items'][0][0]['duplicate_group']
    assert result['items'][1][0]['duplicate_of'] == result['items'][0][0]['id']


def test_batch_callback_exception_propagates_after_commit_without_becoming_file_error(tmp_path):
    db, registry = make_registry(tmp_path)
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(3)]

    def fail_callback(items):
        raise RuntimeError('batch consumer failed')

    with pytest.raises(RuntimeError, match='batch consumer failed'):
        registry.register_many(paths, batch_size=1, on_batch=fail_callback)
    assert [row['path'] for row in db.get_all_videos()] == paths[:1]
    assert db.execute_query('SELECT COUNT(*) n FROM media_discoveries')[0]['n'] == 1


def test_prefetch_overlaps_commit_callback_but_only_submits_current_and_next_batch(tmp_path, monkeypatch):
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(8)]
    initial_started = threading.Barrier(4)
    callback_entered, release_callback, release_next = (threading.Event() for _ in range(3))
    next_started = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    started, submitted, callback_threads, progress_threads = [], [], [], []

    class ObservedExecutor(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            submitted.append(args[-1])
            return super().submit(fn, *args, **kwargs)

    def hasher(path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            started.append(path)
        if path in paths[:4]:
            initial_started.wait(timeout=4)
        if path in paths[2:4]:
            next_started.set()
            assert release_next.wait(4)
        digest = full_hash(path)
        with lock:
            active -= 1
        return digest

    def callback(items):
        callback_threads.append(threading.get_ident())
        if len(callback_threads) == 1:
            assert next_started.wait(4), '首批提交回调时下一批哈希应已经启动'
            callback_entered.set()
            assert release_callback.wait(4)

    _, registry = make_registry(tmp_path, hasher=hasher)
    monkeypatch.setattr('core.media_registry.ThreadPoolExecutor', ObservedExecutor)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        future = coordinator.submit(registry.register_many, paths, workers=100, batch_size=2,
            on_batch=callback, progress=lambda *args: progress_threads.append(threading.get_ident()))
        try:
            assert callback_entered.wait(4)
            assert submitted == paths[:4], '回调阻塞期间不能提前提交第三、四批'
            assert set(started) == set(paths[:4])
            assert peak == 4
        finally:
            release_next.set()
            release_callback.set()
        result = future.result(timeout=8)
    assert not result['errors']
    assert [row['path'] for row, _ in result['items']] == paths
    assert peak <= 4
    assert len(set(callback_threads + progress_threads)) == 1


def test_prefetched_file_changed_in_previous_commit_callback_is_rejected(tmp_path):
    paths = [make_file(tmp_path, f'{i}.mp4', bytes([i])) for i in range(2)]
    prefetched = threading.Event()

    def hasher(path):
        digest = full_hash(path)
        return digest

    db, registry = make_registry(tmp_path, hasher=hasher)
    original_prepare = registry._prepare_fingerprint

    def prepare(path):
        result = original_prepare(path)
        if path == paths[1]:
            prefetched.set()
        return result

    registry._prepare_fingerprint = prepare

    def callback(items):
        if items and items[0][0]['path'] == paths[0]:
            assert prefetched.wait(4)
            with open(paths[1], 'ab') as stream:
                stream.write(b'changed after next batch hash')

    result = registry.register_many(paths, batch_size=1, on_batch=callback)
    assert [row['path'] for row, _ in result['items']] == paths[:1]
    assert len(result['errors']) == 1 and result['errors'][0]['path'] == paths[1]
    assert '校验后' in result['errors'][0]['reason']
    assert len(db.get_all_videos()) == 1
