# -*- coding: utf-8 -*-
"""抽帧颜色通道：OpenCV imencode 必须吃 BGR，误转 RGB 会导致偏蓝。"""
import base64
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from core.video_organizer_service import VideoProcessor


def test_encode_bgr_preserves_red_channel_dominance():
    """纯红 BGR 帧编码后解码，红色通道应明显高于蓝色。"""
    # OpenCV BGR：蓝=0, 绿=0, 红=255
    bgr = np.zeros((32, 32, 3), dtype=np.uint8)
    bgr[:, :] = (0, 0, 255)
    b64 = VideoProcessor.encode_image_to_base64(bgr, quality=95)
    assert b64
    img = Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
    arr = np.asarray(img)
    r_mean = float(arr[:, :, 0].mean())
    b_mean = float(arr[:, :, 2].mean())
    assert r_mean > 200
    assert b_mean < 50


def test_encode_after_wrong_rgb_swap_would_look_blue():
    """对照：若先 BGR→RGB 再 imencode，纯红会变成偏蓝（回归说明根因）。"""
    bgr = np.zeros((32, 32, 3), dtype=np.uint8)
    bgr[:, :] = (0, 0, 255)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    # 错误路径（旧 bug）
    ok, buf = cv2.imencode(".jpg", rgb, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    assert ok
    img = Image.open(BytesIO(buf.tobytes())).convert("RGB")
    arr = np.asarray(img)
    r_mean = float(arr[:, :, 0].mean())
    b_mean = float(arr[:, :, 2].mean())
    # 红蓝对调后「看起来」是蓝
    assert b_mean > r_mean


def test_resize_then_encode_pipeline_uses_bgr_not_rgb_swap(tmp_path, monkeypatch):
    """真实抽帧入口必须保留Capture提供的BGR颜色。"""
    bgr = np.zeros((64, 64, 3), dtype=np.uint8)
    bgr[:, :] = (0, 0, 255)
    class Capture:
        released = False
        def get(self, key): return 1
        def set(self, key, value): return True
        def read(self): return True, bgr.copy()
        def release(self): self.released = True
    capture = Capture()
    monkeypatch.setattr(cv2, 'VideoCapture', lambda _: capture)
    path = tmp_path / 'red.mp4'
    path.write_bytes(b'synthetic')
    result = VideoProcessor().extract_frames(str(path), max_frames=1, target_size=32, use_scene_detection=False)
    assert capture.released
    assert len(result['frames']) == 1
    b64 = result['frames'][0]
    img = Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
    arr = np.asarray(img)
    assert img.size == (32, 32)
    assert float(arr[:, :, 0].mean()) > 200
    assert float(arr[:, :, 2].mean()) < 50
