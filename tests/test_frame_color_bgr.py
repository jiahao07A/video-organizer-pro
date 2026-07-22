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


def test_resize_then_encode_pipeline_uses_bgr_not_rgb_swap():
    """模拟 extract_frames 正确管线：resize(BGR) → encode，不转 RGB。"""
    bgr = np.zeros((64, 64, 3), dtype=np.uint8)
    bgr[:, :] = (0, 0, 255)
    resized = VideoProcessor.resize_image(bgr, 32)
    b64 = VideoProcessor.encode_image_to_base64(resized, quality=95)
    img = Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
    arr = np.asarray(img)
    assert float(arr[:, :, 0].mean()) > float(arr[:, :, 2].mean())