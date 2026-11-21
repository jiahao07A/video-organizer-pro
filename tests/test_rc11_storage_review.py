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
