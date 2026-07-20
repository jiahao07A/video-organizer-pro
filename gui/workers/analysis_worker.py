# -*- coding: utf-8 -*-
from PySide6.QtCore import QThread, Signal
from core.video_organizer_service import VideoOrganizerService

class AnalysisWorker(QThread):
    """异步分析 Worker"""
    progress_updated = Signal(int, str)
    task_finished = Signal(bool, str)

    def __init__(self, service: VideoOrganizerService, input_path):
        super().__init__()
        self.service = service
        self.input_path = input_path

    def run(self):
        try:
            # 更新 service 的回调以配合信号
            self.service.on_log = self.handle_log
            self.service.on_progress = self.handle_progress
            
            self.service.run_analysis(self.input_path)
            self.task_finished.emit(True, "分析任务已完成")
        except Exception as e:
            self.task_finished.emit(False, f"分析出错: {str(e)}")

    def handle_log(self, msg):
        self.progress_updated.emit(-1, msg)

    def handle_progress(self, current, total):
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_updated.emit(progress, f"正在处理: {current}/{total}")
