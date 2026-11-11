# -*- coding: utf-8 -*-
import os
from core.diagnostic_logging import bind_worker, worker_run, worker_exception
from PySide6.QtCore import QObject, Signal, QRunnable, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

class ThumbnailLoaderSignals(QObject):
    loaded = Signal(str, QImage)

class ThumbnailLoader(QRunnable):
    def __init__(self, path, target_size, video_path=None):
        super().__init__()
        bind_worker(self)
        self.video_path = video_path
        self.path = path
        self.target_size = target_size
        self.signals = ThumbnailLoaderSignals()

    @worker_run
    def run(self):
        image = QImage()
        try:
            image = self._read_image(self.path)
            if image.isNull() and self.video_path and os.path.isfile(self.video_path):
                # 仅修复可再生的本地图片缓存；不会调用AI、更新分析结果或写素材。
                from core.video_organizer_service import VideoProcessor
                result = VideoProcessor(os.path.dirname(self.path)).extract_frames(
                    self.video_path, max_frames=1, save_thumbnail=True,
                    use_scene_detection=False,
                )
                recovered = result.get("thumbnail")
                if recovered:
                    image = self._read_image(recovered)
                    if not image.isNull() and recovered != self.path:
                        # 保持旧库引用有效，避免下一次启动反复生成。
                        image.save(self.path)
        except Exception:
            worker_exception()
            image = QImage()
        finally:
            # 失败也必须结束loading状态，避免一直空白且无法重新加载。
            self.signals.loaded.emit(self.path, image)

    def _read_image(self, path):
        if not path or not os.path.isfile(path):
            return QImage()
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        source_size = reader.size()
        target = self.target_size
        if source_size.isValid() and target.isValid():
            scale = min(
                target.width() / max(1, source_size.width()),
                target.height() / max(1, source_size.height()),
            )
            reader.setScaledSize(QSize(
                max(1, round(source_size.width() * scale)),
                max(1, round(source_size.height() * scale)),
            ))
        image = reader.read()
        if not image.isNull() and target.isValid():
            image = image.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return image
