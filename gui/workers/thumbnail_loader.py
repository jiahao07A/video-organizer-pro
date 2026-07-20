# -*- coding: utf-8 -*-
import os
from PySide6.QtCore import QObject, Signal, QRunnable, QSize, Qt
from PySide6.QtGui import QImage

class ThumbnailLoaderSignals(QObject):
    loaded = Signal(str, QImage)

class ThumbnailLoader(QRunnable):
    def __init__(self, path, target_size):
        super().__init__()
        self.path = path
        self.target_size = target_size
        self.signals = ThumbnailLoaderSignals()

    def run(self):
        if not self.path or not os.path.exists(self.path):
            return
        image = QImage(self.path)
        if not image.isNull():
            # Scale in thread
            scaled = image.scaled(self.target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.signals.loaded.emit(self.path, scaled)
