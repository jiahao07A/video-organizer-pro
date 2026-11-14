"""扫描进度快照；只报告已完成工作，不用阶段权重预测剩余时间。"""
from __future__ import annotations

import time


class ScanProgress:
    LABELS = {'discovery': '发现视频', 'history': '校验历史指纹',
              'fingerprint': '校验完整指纹', 'register': '登记与去重',
              'thumbnail': '生成缩略图', 'complete': '扫描完成'}

    def __init__(self, callback=None):
        self.callback = callback
        self.started = time.monotonic()
        self.last_sent = 0.0
        self.last_phase = None
        self.percent = 0
        self.counts = dict(files_found=0, new_count=0, duplicate_count=0, error_count=0)
        self.snapshot = {}

    def emit(self, phase, completed=0, total=0, *, percent=None, force=False, **counts):
        self.counts.update(counts)
        now = time.monotonic()
        if percent is not None:
            self.percent = max(self.percent, min(1000, int(percent)))
        self.snapshot = dict(phase=phase, phase_label=self.LABELS[phase],
            completed=completed, total=total, percent=self.percent,
            elapsed_seconds=now - self.started, **self.counts)
        if self.callback and (force or phase != self.last_phase or now - self.last_sent >= 0.1):
            self.callback(dict(self.snapshot))
            self.last_sent, self.last_phase = now, phase
