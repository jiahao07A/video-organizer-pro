# -*- coding: utf-8 -*-
"""详情预览区：应用内素材预览（播放/暂停/进度/音量/倍速/定位文件）。"""
from __future__ import annotations

import os
import sys
import subprocess

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QLabel,
    QComboBox,
    QSizePolicy,
)

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget

    HAS_MULTIMEDIA = True
except Exception:
    HAS_MULTIMEDIA = False


class MediaPreviewWidget(QWidget):
    """嵌入详情面板的素材预览控件。"""

    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = None
        self._player = None
        self._audio = None
        self._duration = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        if HAS_MULTIMEDIA:
            self.video_widget = QVideoWidget()
            self.video_widget.setMinimumHeight(200)
            self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout.addWidget(self.video_widget, 1)

            self._audio = QAudioOutput()
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self.video_widget)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)
            self._player.playbackStateChanged.connect(self._on_state)
        else:
            self.placeholder = QLabel("当前环境无多媒体组件\n可点下方「打开文件夹」或使用系统播放器")
            self.placeholder.setAlignment(Qt.AlignCenter)
            self.placeholder.setMinimumHeight(180)
            self.placeholder.setStyleSheet(
                "border: 2px dashed #666; border-radius: 8px; background: #1a1a1a;"
            )
            layout.addWidget(self.placeholder, 1)

        # 进度
        prog_row = QHBoxLayout()
        self.time_label = QLabel("00:00 / 00:00")
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self._seek)
        prog_row.addWidget(self.seek_slider, 1)
        prog_row.addWidget(self.time_label)
        layout.addLayout(prog_row)

        # 控制
        ctrl = QHBoxLayout()
        self.play_btn = QPushButton("播放")
        self.play_btn.clicked.connect(self.toggle_play)
        self.play_btn.setEnabled(False)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(self._set_volume)

        self.speed_combo = QComboBox()
        for s in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            self.speed_combo.addItem(f"{s}x", s)
        self.speed_combo.setCurrentIndex(2)
        self.speed_combo.currentIndexChanged.connect(self._set_speed)

        self.reveal_btn = QPushButton("在文件夹中显示")
        self.reveal_btn.clicked.connect(self.reveal_in_explorer)
        self.reveal_btn.setEnabled(False)

        ctrl.addWidget(self.play_btn)
        ctrl.addWidget(QLabel("音量"))
        ctrl.addWidget(self.volume_slider)
        ctrl.addWidget(QLabel("倍速"))
        ctrl.addWidget(self.speed_combo)
        ctrl.addStretch()
        ctrl.addWidget(self.reveal_btn)
        layout.addLayout(ctrl)

        self._set_volume(80)

    def set_media(self, path: str | None):
        self.stop()
        self._path = path if path and os.path.isfile(path) else None
        self.play_btn.setEnabled(bool(self._path) and HAS_MULTIMEDIA)
        self.reveal_btn.setEnabled(bool(self._path))
        if not self._path:
            return
        if HAS_MULTIMEDIA and self._player:
            self._player.setSource(QUrl.fromLocalFile(self._path))
            self.play_btn.setText("播放")

    def toggle_play(self):
        if not HAS_MULTIMEDIA or not self._player or not self._path:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def stop(self):
        if HAS_MULTIMEDIA and self._player:
            self._player.stop()
        self.play_btn.setText("播放")

    def _on_state(self, state):
        if not HAS_MULTIMEDIA:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText("暂停")
        else:
            self.play_btn.setText("播放")

    def _on_duration(self, d):
        self._duration = d
        self.seek_slider.setRange(0, max(0, d))
        self._update_time_label(self._player.position() if self._player else 0)

    def _on_position(self, pos):
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(pos)
        self._update_time_label(pos)

    def _seek(self, pos):
        if HAS_MULTIMEDIA and self._player:
            self._player.setPosition(pos)

    def _set_volume(self, v):
        if HAS_MULTIMEDIA and self._audio:
            self._audio.setVolume(max(0.0, min(1.0, v / 100.0)))

    def _set_speed(self, _idx=None):
        if HAS_MULTIMEDIA and self._player:
            rate = float(self.speed_combo.currentData() or 1.0)
            self._player.setPlaybackRate(rate)

    def _fmt(self, ms: int) -> str:
        s = max(0, int(ms // 1000))
        return f"{s // 60:02d}:{s % 60:02d}"

    def _update_time_label(self, pos):
        self.time_label.setText(f"{self._fmt(pos)} / {self._fmt(self._duration)}")

    def reveal_in_explorer(self):
        path = self._path
        if not path or not os.path.exists(path):
            self.status_message.emit("文件不存在，无法定位")
            return
        path = os.path.normpath(path)
        try:
            if sys.platform == "win32":
                subprocess.run(["explorer", "/select,", path], check=False)
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", path], check=False)
            else:
                folder = os.path.dirname(path)
                subprocess.run(["xdg-open", folder], check=False)
        except Exception as e:
            self.status_message.emit(f"定位失败: {e}")