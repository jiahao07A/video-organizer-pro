# -*- coding: utf-8 -*-
from PySide6.QtCore import QThread, Signal
from core.video_organizer_service import VideoOrganizerService

class RenameWorker(QThread):
    """异步重命名 Worker"""
    progress_updated = Signal(int, str)
    task_finished = Signal(bool, str)

    def __init__(self, service: VideoOrganizerService, dry_run=True, selected_paths=None, 
                 pattern=None, regex_find=None, regex_replace=""):
        super().__init__()
        self.service = service
        self.dry_run = dry_run
        self.selected_paths = selected_paths
        self.pattern = pattern
        self.regex_find = regex_find
        self.regex_replace = regex_replace

    def run(self):
        try:
            self.service.on_log = self.handle_log
            self.service.batch_rename(
                dry_run=self.dry_run, 
                selected_paths=self.selected_paths,
                custom_pattern=self.pattern,
                regex_find=self.regex_find,
                regex_replace=self.regex_replace
            )
            msg = "模拟重命名完成" if self.dry_run else "重命名应用完成"
            self.task_finished.emit(True, msg)
        except Exception as e:
            self.task_finished.emit(False, f"重命名出错: {str(e)}")

    def handle_log(self, msg):
        self.progress_updated.emit(-1, msg)
