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


@pytest.mark.parametrize('extension', ['mkv', 'mp4'])
@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='需要 FFmpeg 工具链')
def test_injected_multitrack_keeps_container_and_audio(tmp_path, extension):
    path = tmp_path / f'双轨.{extension}'
    metadata_args = []
    if extension == 'mkv':
        chapters = tmp_path / 'chapters.ffmeta'
        chapters.write_text(';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\ntitle=段落\n', encoding='utf-8')
        metadata_args = ['-f', 'ffmetadata', '-i', str(chapters), '-map_chapters', '3']
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=48x48:r=8',
                    '-f', 'lavfi', '-i', 'sine=frequency=440',
                    '-f', 'lavfi', '-i', 'sine=frequency=880', *metadata_args, '-t', '0.5', '-map', '0:v', '-map', '1:a',
                    '-map', '2:a', '-c:v', 'mpeg4', '-c:a', 'aac', '-metadata', 'title=素材标题',
                    '-metadata:s:a:0', 'language=eng', '-metadata:s:a:1', 'language=fra',
                    str(path)], check=True, capture_output=True, timeout=30)
    def read():
        return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_chapters', '-show_format',
                                                   '-of', 'json', str(path)], timeout=30))
    before = read()
    assert MetadataInjector.inject_tags(str(path), ['声音'], '双语采访')
    after = read()
    assert before['format']['format_name'] == after['format']['format_name']
    assert [(s['codec_name'], s.get('tags', {}).get('language')) for s in after['streams']] == [
        (s['codec_name'], s.get('tags', {}).get('language')) for s in before['streams']]
    tags = {k.lower(): v for k,v in after['format']['tags'].items()}
    assert tags['comment'] == '双语采访 | Tags: 声音' and tags['title'] == '素材标题'
    if extension == 'mkv':
        assert after['chapters'][0]['tags']['title'] == '段落'
        assert float(after['chapters'][0]['end_time']) == .5
    else:
        disguised = tmp_path / '后缀与容器不符.flv'
        path.rename(disguised)
        original_bytes = disguised.read_bytes()
        assert not MetadataInjector.inject_tags(str(disguised), ['不兼容'], '必须保留原件')
        assert disguised.read_bytes() == original_bytes


def test_discovery_reads_once_but_later_gate_rehashes(service, tmp_path):
    path = tmp_path / '数据.mp4'
    path.write_bytes(b'first')
    with patch.object(service.media_registry, 'hasher', wraps=full_hash) as hashed:
        assert service.filter_processable_paths([str(path)], record_discovery=True) == [str(path)]
        assert hashed.call_count == 1
        path.write_bytes(b'other')
        assert service.filter_processable_paths([str(path)]) == [str(path)]
        assert hashed.call_count == 2
    assert service.db.get_video_by_path(str(path))['file_hash'] == full_hash(str(path))


def test_discovery_error_cannot_bypass_fingerprint(service, tmp_path):
    path = tmp_path / 'err.mp4'
    path.write_bytes(b'first')
    with patch.object(service.media_registry, 'hasher', side_effect=OSError('synthetic unavailable')):
        with pytest.raises(OSError):
            service.filter_processable_paths([str(path)], record_discovery=True, refresh_pending=False)


def test_missing_ffprobe_keeps_source_and_analysis_and_notifies_user(service, tmp_path, monkeypatch):
    source = tmp_path / '媒体.mp4'
    source.write_bytes(b'synthetic original')
    notices = []
    service.on_log = notices.append
    service.log('前一条进度')
    monkeypatch.setenv('PATH', str(tmp_path))
    service._persist_current_analysis({'path': str(source), 'filename': source.name, 'status': 'analyzed',
                                       'tags': ['测试'], 'category': '课堂', 'summary': '讲解',
                                       '_inject_metadata': True})
    row = service.db.get_video_by_path(str(source))
    assert row['status'] == 'analyzed' and row['summary'] == '讲解' and not row['metadata_injected']
    assert source.read_bytes() == b'synthetic original'
    assert any('未写入源文件' in line and '分析数据' in line and 'ffprobe' in line for line in notices)


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='需要 FFmpeg 工具链')
def test_source_changed_during_metadata_write_cannot_commit_stale_analysis(service, tmp_path, monkeypatch):
    source = tmp_path / '并发.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=48x48:r=8',
                    '-t', '0.5', '-c:v', 'mpeg4', str(source)], check=True, capture_output=True, timeout=30)
    service.settings['processing'].update(enable_metadata_injection=True, enable_audio_transcription=False,
                                         enable_scene_detection=False, max_workers=1, max_frames=2)
    calls = []
    def answer(*args, **kwargs):
        calls.append(1)
        return {'category': 'Other', 'summary': '旧画面的摘要', 'tags': []}
    monkeypatch.setattr(service.ai, 'analyze_video', answer)
    original_probe = MetadataInjector._probe
    def modify_when_output_ready(name):
        info = original_probe(name)
        if '.video-organizer-metadata-' in name:
            with source.open('ab') as handle:
                handle.write(b'external modification')
        return info
    monkeypatch.setattr(MetadataInjector, '_probe', modify_when_output_ready)
    result = service.run_analysis([str(source)])
    row = service.db.get_video_by_path(str(source))
    assert result['succeeded'] == 0 and result['failed'] == 1
    assert calls == [1]  # 文件变更不能自动重复付费分析。
    assert row['file_hash'] == full_hash(str(source))
    assert row['summary'] != '旧画面的摘要'
    assert row['fingerprint_state'] == 'review'
    assert source.read_bytes().endswith(b'external modification')


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
