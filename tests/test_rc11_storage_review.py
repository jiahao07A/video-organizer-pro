"""RC11：流保真、指纹读取边界、监控扫描和目录分页。"""
import json
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

from core.media_registry import full_hash
from core.media_watch import MediaDirectoryMonitor
from core.video_catalog import VideoQuery
from core.video_organizer_service import MetadataInjector, SettingsManager, VideoOrganizerService


@pytest.fixture
def service(tmp_path):
    return VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), db_path=str(tmp_path / 'review.db'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows 路径语义')
def test_unicode_scope_directory_file_and_exclusion(service, tmp_path):
    folder = tmp_path / 'Übung'
    keep = str(folder / 'Ö1.mp4')
    drop = str(folder / 'Ä2.mp4')
    neighbor = str(tmp_path / 'Übung其他' / 'Ö1.mp4')
    service.db.insert_videos_if_absent([{'path': p} for p in (keep, drop, neighbor)])
    q = VideoQuery(scope_paths=(str(folder).lower(),), excluded_paths=(drop.lower(),))
    assert service.catalog.resolve_visible_paths(q) == [keep]
    assert service.catalog.resolve_visible_paths(VideoQuery(scope_paths=(drop.lower(),))) == [drop]
    assert service.db.get_video_by_path(keep.lower())['path'] == keep
    assert [r['path'] for r in service.db.get_videos_by_paths([drop.lower()])] == [drop]
    assert service.db.reconcile_monitored_paths(str(folder).lower(), [keep.lower()])
    assert service.db.monitored_status_counts(str(folder))['缺失'] == 1
    assert service.db.get_video_by_path(neighbor)['is_missing'] == 0


def test_plain_pages_keep_count_and_use_sort_index(service, tmp_path):
    service.db.insert_videos_if_absent([{'path': str(tmp_path / f'{i}.mp4'), 'timestamp': '2026-01-01'}
                                        for i in range(3)])
    statements = []
    execute = service.db.execute_query
    def recording(query, params=()):
        statements.append((query,params))
        return execute(query,params)
    with patch.object(service.db, 'execute_query', side_effect=recording):
        first = service.catalog.list_page(limit=1, offset=1)
        empty = service.catalog.list_page(limit=1, offset=4)
    assert first.total_count == empty.total_count == 3 and not empty.rows
    assert [r['id'] for r in first.rows] == [2]
    assert len(statements) == 2
    details = [r['detail'] for r in execute('EXPLAIN QUERY PLAN '+statements[0][0], statements[0][1])]
    assert any('idx_videos_timestamp_id' in s for s in details)


def test_unchanged_watch_scan_opens_bounded_connections(service, tmp_path):
    root = tmp_path / 'incoming'
    root.mkdir()
    records = []
    for i in range(32):
        p = root / f'{i}.mp4'
        p.write_bytes(str(i).encode())
        records.append(str(p))
    service.media_registry.register_many(records)
    monitor = MediaDirectoryMonitor(service, {'auto_analyze': False, 'directories': [{'path': str(root)}]})
    try:
        with patch.object(service.db, '_get_connection', wraps=service.db._get_connection) as opens:
            monitor.scan_now()
        assert opens.call_count <= 8
        assert all(service.db.get_video_by_path(p)['fingerprint_state'] == 'verified' for p in records)
    finally:
        monitor.stop(shutdown_analysis=True)
