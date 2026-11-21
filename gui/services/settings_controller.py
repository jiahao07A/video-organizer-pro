# -*- coding: utf-8 -*-
"""按存储目标聚合编辑；失败目标保留待保存状态，重试不重复写成功目标。"""
from __future__ import annotations

import copy
from PySide6.QtCore import QObject, QTimer, Signal
from core.video_organizer_service import SettingsManager


class SettingsController(QObject):
    saved = Signal(str)
    save_failed = Signal(str)
    pending = Signal()
    target_changed = Signal(str, str)

    def __init__(self, service, parent=None, debounce_ms=800):
        super().__init__(parent)
        self.service = service
        self._debounce_ms = max(0, int(debounce_ms))
        self._dirty = False
        self._collecting = False
        self._collector = self._validator = self._after_save = None
        self._targets = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._debounce_ms)
        self._timer.timeout.connect(self.flush)
        self.register_target("settings", "设置文件", lambda: service.settings,
                             lambda: SettingsManager.save_settings(service.settings, service.db))

    @property
    def debounce_ms(self):
        return self._debounce_ms

    def register_target(self, key, label, collector, writer):
        self._targets[key] = dict(label=label, collector=collector, writer=writer,
                                  saved=copy.deepcopy(collector()), state="saved")

    def _collect(self):
        if self._collecting:
            return True
        self._collecting = True
        try:
            if callable(self._collector) and self._collector() is False:
                raise ValueError("invalid settings")
            if callable(self._validator) and self._validator() is False:
                raise ValueError("invalid settings")
            return True
        except Exception:
            self.save_failed.emit("设置校验失败，请检查输入；编辑内容已保留。")
            return False
        finally:
            self._collecting = False

    def schedule_save(self, collector=None, validator=None, after_save=None):
        if self._collecting:
            return
        if collector is not None:
            self._collector = collector
        self._validator = validator
        self._after_save = after_save
        self._dirty = True
        self.pending.emit()
        if self._collect():
            for key, target in self._targets.items():
                if target["collector"]() != target["saved"]:
                    target["state"] = "pending"
                    self.target_changed.emit(key, "pending")
        self._timer.start()

    def flush_now(self):
        return self.flush()

    def flush(self):
        self._timer.stop()
        if not self._dirty:
            return True
        if not self._collect():
            return False
        failures = []
        for key, target in self._targets.items():
            snapshot = copy.deepcopy(target["collector"]())
            if snapshot == target["saved"] and target["state"] != "failed":
                continue
            try:
                if target["writer"]() is False:
                    raise OSError("write failed")
                target["saved"] = snapshot
                target["state"] = "saved"
            except Exception:
                target["state"] = "failed"
                failures.append(target["label"])
            self.target_changed.emit(key, target["state"])
        if failures:
            self.save_failed.emit("、".join(failures) + "保存失败；编辑内容已保留，请重试。")
            return False
        try:
            if callable(self._after_save) and self._after_save() is False:
                raise RuntimeError("apply failed")
        except Exception:
            self.save_failed.emit("设置已保存，但应用失败，请重试。")
            return False
        self._dirty = False
        self.saved.emit("设置已自动保存")
        return True

    def cancel(self):
        self._timer.stop()
        self._dirty = False
        self._collector = self._validator = self._after_save = None
