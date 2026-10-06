# -*- coding: utf-8 -*-
import os
from PySide6.QtCore import QObject, Signal, QRunnable, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

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

        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        source_size = reader.size()
        target = self.target_size
        if source_size.isValid() and target.isValid():
            scale = min(
                target.width() / max(1, source_size.width()),
                target.height() / max(1, source_size.height()),
            )
            reader.setScaledSize(
                QSize(
                    max(1, round(source_size.width() * scale)),
                    max(1, round(source_size.height() * scale)),
                )
            )
            image = reader.read()
        else:
            # Some codecs do not expose dimensions until decoding. Keep a
            # safe fallback for those files and scale only after the read.
            image = QImage(self.path)
            if not image.isNull() and target.isValid():
                image = image.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        if not image.isNull():
            self.signals.loaded.emit(self.path, image)
