"""首帧缩略图真实编解码回归，只使用临时合成素材。"""
import hashlib
from pathlib import Path
import shutil
import subprocess

import av
import cv2
import numpy as np
import pytest

from core.video_organizer_service import VideoProcessor


def make_video(path, width=800, height=480):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:height // 2, :width // 2] = (0, 0, 240)
    frame[:height // 2, width // 2:] = (0, 220, 0)
    frame[height // 2:, :width // 2] = (235, 0, 0)
    frame[height // 2:, width // 2:] = (0, 220, 220)
    with av.open(str(path), 'w') as output:
        stream = output.add_stream('mpeg4', rate=10)
        stream.width, stream.height, stream.pix_fmt = width, height, 'yuv420p'
        for _ in range(2):
            video_frame = av.VideoFrame.from_ndarray(frame, format='bgr24')
            for packet in stream.encode(video_frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


def read_image(path):
    # imdecode 支持 Windows Unicode 文件名，不依赖 OpenCV窄字符路径。
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def corners(image):
    height, width = image.shape[:2]
    return np.array([image[int(height * y), int(width * x)].astype(float)
                     for y, x in ((.25, .25), (.25, .75), (.75, .25), (.75, .75))])


def forbid_ai(monkeypatch, processor):
    def forbidden(*_args, **_kwargs):
        pytest.fail('入库缩略图不得调用AI帧提取、base64或感知哈希')
    monkeypatch.setattr(processor, 'extract_frames', forbidden)
    monkeypatch.setattr(processor, 'encode_image_to_base64', forbidden)
    monkeypatch.setattr(processor, 'calculate_phash', forbidden)
    monkeypatch.setattr('core.video_organizer_service.base64.b64encode', forbidden)
    monkeypatch.setattr('core.video_organizer_service.imagehash.phash', forbidden)


@pytest.mark.parametrize('size', [(800, 480), (160, 96)])
def test_pyav_unicode_thumbnail_preserves_color_orientation_and_source(tmp_path, monkeypatch, size):
    source = make_video(tmp_path / 'Ä中文素材.mp4', *size)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / '中文缩略图'
    processor = VideoProcessor(str(destination))
    forbid_ai(monkeypatch, processor)
    monkeypatch.setattr(cv2, 'VideoCapture', lambda *_args, **_kwargs:
                        pytest.fail('真实PyAV可解码时不应走OpenCV回退'))
    thumbnail = processor.extract_thumbnail(str(source), target_size=512)
    assert thumbnail and Path(thumbnail).parent == destination
    image = read_image(thumbnail)
    assert max(image.shape[:2]) == min(512, max(size))
    assert image.shape[1] > image.shape[0]
    expected = np.array([(0, 0, 240), (0, 220, 0), (235, 0, 0), (0, 220, 220)])
    assert np.max(np.abs(corners(image) - expected)) < 25
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_av_failure_falls_back_to_real_opencv_and_bad_video_returns_none(tmp_path, monkeypatch):
    source = make_video(tmp_path / 'fallback.mp4')
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    processor = VideoProcessor(str(tmp_path / 'thumbs'))
    forbid_ai(monkeypatch, processor)
    calls = []

    def unavailable(*_args, **_kwargs):
        calls.append(True)
        raise OSError('模拟PyAV不支持此容器')

    monkeypatch.setattr(av, 'open', unavailable)
    image = read_image(processor.extract_thumbnail(str(source), target_size=512))
    assert image.shape[:2] == (307, 512)
    expected = np.array([(0, 0, 240), (0, 220, 0), (235, 0, 0), (0, 220, 220)])
    assert np.max(np.abs(corners(image) - expected)) < 25
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    bad = tmp_path / 'broken.mp4'
    bad.write_bytes(b'not a video')
    assert processor.extract_thumbnail(str(bad)) is None
    assert len(calls) == 2


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_real_display_rotation_matches_existing_opencv_orientation(tmp_path, monkeypatch, rotation):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('真实display matrix夹具需要ffmpeg无损封装')
    source = make_video(tmp_path / 'unrotated.mp4')
    rotated = tmp_path / f'rotation-{rotation}.mp4'
    # PyAV 16.1 写stream.metadata[rotate]不生成display matrix；使用FFmpeg
    # 无损封装PyAV生成的合成文件，明确验证输出确实携带可读rotation。
    subprocess.run([ffmpeg, '-v', 'error', '-display_rotation', str(rotation),
                    '-i', str(source), '-c', 'copy', str(rotated)],
                   check=True, capture_output=True, timeout=15)
    with av.open(str(rotated)) as container:
        decoded = next(container.decode(container.streams.video[0]))
        assert decoded.rotation % 360 == rotation
    before = hashlib.sha256(rotated.read_bytes()).hexdigest()
    cap = cv2.VideoCapture(str(rotated))
    try:
        ok, reference = cap.read()
        assert ok
    finally:
        cap.release()
    processor = VideoProcessor(str(tmp_path / 'thumbs'))
    forbid_ai(monkeypatch, processor)
    monkeypatch.setattr(cv2, 'VideoCapture', lambda *_args, **_kwargs:
                        pytest.fail('旋转图必须由真实PyAV路径处理'))
    image = read_image(processor.extract_thumbnail(str(rotated)))
    assert max(image.shape[:2]) <= 512
    assert (image.shape[1] > image.shape[0]) == (reference.shape[1] > reference.shape[0])
    assert np.max(np.abs(corners(image) - corners(reference))) < 25
    assert hashlib.sha256(rotated.read_bytes()).hexdigest() == before
