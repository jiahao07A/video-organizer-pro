"""监控等待恢复不应受界面分页限制；使用真实临时文件及SQLite。"""
import os
import time

import pytest

from core.media_registry import FILE_WAIT_PREFIX, path_key
from core.media_watch import MediaDirectoryMonitor
from core.video_organizer_service import SettingsManager, VideoOrganizerService
from core.watch_config import normalize_watch_config


@pytest.fixture
def monitored_library(tmp_path):
    service = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'),
        results_json=str(tmp_path / 'results.json'),
        results_csv=str(tmp_path / 'results.csv'),
    )
    root = tmp_path / 'media'
    root.mkdir()
    config = normalize_watch_config({
        'auto_analyze': False, 'batch_size': 1, 'stability_seconds': 0,
        'directories': [{'path': str(root)}],
    })
    monitor = MediaDirectoryMonitor(service, config)
    try:
        paths = []
        for index in range(202):
            path = root / f'clip-{index:03}.mp4'
            path.write_bytes(f'unique synthetic video {index}'.encode())
            monitor._process_video(str(path))
            paths.append(str(path))
        yield service, monitor, paths, config['directories'][0]['id']
    finally:
        monitor.stop(shutdown_analysis=True)


def _item(db, path, task_id):
    return db.execute_query(
        'SELECT * FROM media_watch_items WHERE path=? AND task_id=?', (path, task_id)
    )[0]


def _wait(service, item, stage):
    service.media_registry.update_item(item['id'], stage, FILE_WAIT_PREFIX + 'temporary sharing violation')
    service.db.execute_non_query(
        "UPDATE media_watch_batches SET state='paused' WHERE id=?", (item['batch_id'],)
    )


def _batch_state(db, item):
    return db.execute_query('SELECT state FROM media_watch_batches WHERE id=?', (item['batch_id'],))[0]['state']


@pytest.mark.parametrize('stage', ['queued', 'pending', 'rename_ready', 'xmp_ready'])
def test_old_waiting_item_recovers_after_scan_and_baseline_registration(monitored_library, stage):
    service, monitor, paths, task_id = monitored_library
    db, registry = service.db, service.media_registry
    oldest = _item(db, paths[0], task_id)
    recent = _item(db, paths[-1], task_id)
    _wait(service, oldest, stage)
    # 带相同错误前缀的终态不是可恢复等待项，不能被最近页误判唤醒。
    _wait(service, recent, 'done')
    page = registry.items(task_id)
    assert len(page) == 200
    assert oldest['id'] not in {item['id'] for item in page}
    assert registry.has_waiting_file(task_id, paths[0])
    assert not registry.has_waiting_file(task_id, paths[-1])

    monitor.scan_now()
    monitor._process_stable_files(time.monotonic() + 1)
    restored = _item(db, paths[0], task_id)
    assert restored['stage'] == stage
    assert restored['error'] == ''
    assert _batch_state(db, oldest) == 'queued'
    assert _item(db, paths[-1], task_id)['error'].startswith(FILE_WAIT_PREFIX)
    assert _batch_state(db, recent) == 'paused'

    # 基线历史跳过分支也必须用相同的全量精确查询判断旧等待项。
    _wait(service, oldest, stage)
    db.execute_non_query(
        'INSERT OR IGNORE INTO media_watch_baseline VALUES(?,?)', (task_id, path_key(paths[0]))
    )
    monitor._process_video(paths[0])
    assert _item(db, paths[0], task_id)['error'] == ''
    assert _batch_state(db, oldest) == 'queued'
    assert len(registry.items(task_id)) == 200


def test_waiting_query_isolates_task_path_stage_and_error(monitored_library):
    service, _monitor, paths, task_id = monitored_library
    db, registry = service.db, service.media_registry
    old = _item(db, paths[0], task_id)
    _wait(service, old, 'queued')
    assert registry.has_waiting_file(task_id, paths[0])
    assert not registry.has_waiting_file('another-task', paths[0])
    assert not registry.has_waiting_file(task_id, paths[1])

    for stage in ('done', 'cancelled', 'review', 'duplicate', 'failed', 'analyzing'):
        registry.update_item(old['id'], stage, FILE_WAIT_PREFIX + 'temporary sharing violation')
        assert not registry.has_waiting_file(task_id, paths[0])
    registry.update_item(old['id'], 'queued', 'network timeout')
    assert not registry.has_waiting_file(task_id, paths[0])


@pytest.mark.skipif(os.name != 'nt', reason='Windows路径等价规则仅在Windows验证')
def test_waiting_query_normalizes_windows_unicode_paths(tmp_path):
    service = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / 'catalog.db'), results_json=str(tmp_path / 'results.json'),
        results_csv=str(tmp_path / 'results.csv'),
    )
    path = tmp_path / 'Ä-素材.mp4'
    path.write_bytes(b'unique synthetic content')
    registry = service.media_registry
    row = registry.register(str(path))
    item = registry.enqueue(row, 'task', {'batch_size': 1})
    _wait(service, item, 'queued')
    alternate = str(path).replace('\\', '/').upper()
    assert os.path.samefile(path, alternate)
    assert registry.has_waiting_file('task', alternate)
