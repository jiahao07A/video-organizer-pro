"""元数据注入仅拥有独占临时输出，不覆盖素材邻接文件。"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from core import video_organizer_service as module


def synthetic_source(tmp_path, name='source.mp4'):
    source = tmp_path / name
    source.write_bytes(b'original video sentinel')
    neighbor = Path(str(source) + '.tmp.mp4')
    neighbor.write_bytes(b'user-owned neighboring sentinel')
    return source, neighbor


def assert_owned_output(path, source):
    output = Path(path)
    assert output.parent == source.parent
    assert output.name.startswith('.video-organizer-metadata-')
    assert output.suffix == '.mp4'
    assert output != Path(str(source) + '.tmp.mp4')
    assert output.exists()  # mkstemp 已独占创建；ffmpeg 写入本次拥有的路径。


def test_metadata_injection_preserves_existing_neighbor_and_owns_unique_output(tmp_path, monkeypatch):
    source, neighbor = synthetic_source(tmp_path)
    captured = []

    def run(command, **kwargs):
        assert_owned_output(command[-1], source)
        assert command[:4] == ['ffmpeg', '-y', '-i', str(source)]
        assert command[command.index('-metadata') + 1] == 'comment=中文摘要 | Tags: 标签一,标签二'
        assert command[command.index('-codec') + 1] == 'copy'
        assert kwargs['capture_output'] and kwargs['check']
        captured.append(command[-1])
        Path(command[-1]).write_bytes(b'updated video')

    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.MetadataInjector.inject_tags(str(source), ['标签一', '标签二'], '中文摘要')
    assert source.read_bytes() == b'updated video'
    assert neighbor.read_bytes() == b'user-owned neighboring sentinel'
    assert not Path(captured[0]).exists()
    assert list(tmp_path.glob('.video-organizer-metadata-*')) == []


@pytest.mark.parametrize('failure', ['ffmpeg', 'replace'])
def test_injection_failure_preserves_source_and_neighbor_and_cleans_owned_output(tmp_path, monkeypatch, failure):
    source, neighbor = synthetic_source(tmp_path)
    captured = []

    def run(command, **kwargs):
        assert_owned_output(command[-1], source)
        captured.append(command[-1])
        Path(command[-1]).write_bytes(b'partial owned output')
        if failure == 'ffmpeg':
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(module.subprocess, 'run', run)
    if failure == 'replace':
        monkeypatch.setattr(module.os, 'replace', lambda *a: (_ for _ in ()).throw(PermissionError('source locked')))
    assert not module.MetadataInjector.inject_tags(str(source), ['测试'], '摘要')
    assert source.read_bytes() == b'original video sentinel'
    assert neighbor.read_bytes() == b'user-owned neighboring sentinel'
    assert len(captured) == 1 and not Path(captured[0]).exists()
    assert list(tmp_path.glob('.video-organizer-metadata-*')) == []


def test_parallel_different_sources_have_distinct_owned_temporary_files(tmp_path, monkeypatch):
    sources = [synthetic_source(tmp_path, name)[0] for name in ('first.mp4', 'second.mp4')]
    barrier = threading.Barrier(2)
    outputs = []

    def run(command, **kwargs):
        source = Path(command[3])
        assert_owned_output(command[-1], source)
        outputs.append(command[-1])
        barrier.wait(timeout=3)
        Path(command[-1]).write_bytes(b'updated ' + source.name.encode())

    monkeypatch.setattr(module.subprocess, 'run', run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda source: module.MetadataInjector.inject_tags(str(source), [], '并发'), sources))
    assert results == [True, True]
    assert len(set(outputs)) == 2
    for source in sources:
        assert source.read_bytes() == b'updated ' + source.name.encode()
        assert Path(str(source) + '.tmp.mp4').read_bytes() == b'user-owned neighboring sentinel'
    assert list(tmp_path.glob('.video-organizer-metadata-*')) == []


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='真实音视频验证需要ffmpeg与ffprobe')
def test_real_ffmpeg_preserves_neighbor_complete_chinese_comment_and_decodable_streams(tmp_path):
    source = tmp_path / '中文音视频.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
        '-i', 'color=c=blue:s=160x90:r=10', '-f', 'lavfi',
        '-i', 'sine=frequency=880:sample_rate=44100:duration=1', '-t', '1',
        '-c:v', 'mpeg4', '-c:a', 'aac', '-shortest', str(source)],
        capture_output=True, check=True, timeout=30)
    neighbor = Path(str(source) + '.tmp.mp4')
    neighbor.write_bytes(b'preexisting mp4 neighbor must survive')
    summary = '中文摘要：蓝色画面与正弦音轨，验证安全写入。'
    tags = ['平静', '人物与城市', '测试 & 验证', '中文标签']
    assert module.MetadataInjector.inject_tags(str(source), tags, summary)
    details = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
        '-of', 'json', str(source)], capture_output=True, check=True, timeout=30).stdout.decode('utf-8'))
    assert details['format']['tags']['comment'] == f'{summary} | Tags: {",".join(tags)}'
    assert {stream['codec_type'] for stream in details['streams']} == {'video', 'audio'}
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(source), '-map', '0', '-f', 'null', '-'],
        capture_output=True, check=True, timeout=30)
    assert neighbor.read_bytes() == b'preexisting mp4 neighbor must survive'
    assert list(tmp_path.glob('.video-organizer-metadata-*')) == []
