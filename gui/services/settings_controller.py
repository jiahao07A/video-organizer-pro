# -*- coding: utf-8 -*-
"""设置控制器：普通界面偏好的防抖保存与状态反馈。

职责边界：
- 只负责把「普通偏好」写回 settings 并防抖落盘，然后发出状态信号；
- 不负责敏感配置（API Key / 路由 / 供应商）的保存，那些仍由设置页的明确保存区处理；
- 不改变任何配置键，最终仍调用 ``SettingsManager.save_settings``。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from core.video_organizer_service import SettingsManager


class SettingsController(QObject):
    """聚合界面偏好的防抖保存，并对外报告保存状态。"""

    #: 保存成功：为简明状态文案
    saved = Signal(str)
    #: 保存失败：为错误文案
    save_failed = Signal(str)
    #: 有改动待保存（用于显示「正在保存…」）
    pending = Signal()

    def __init__(self, service, parent=None, debounce_ms: int = 800):
        super().__init__(parent)
        self.service = service
        self._debounce_ms = max(0, int(debounce_ms))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._debounce_ms)
        self._timer.timeout.connect(self.flush)

    @property
    def debounce_ms(self) -> int:
        return self._debounce_ms

    def schedule_save(self, mutate=None) -> None:
        """登记一次偏好改动并重启防抖计时。

        ``mutate`` 在写盘前调用，用于把控件当前值同步进 settings。
        """
        if callable(mutate):
            mutate()
        self.pending.emit()
        self._timer.start()

    def flush(self) -> bool:
        """立即写盘（若已有待保存改动）。返回是否成功。"""
        had_pending = self._timer.isActive()
        self._timer.stop()
        try:
            SettingsManager.save_settings(self.service.settings, self.service.db)
        except Exception as exc:  # noqa: BLE001 - 保存失败需反馈给用户
            self.save_failed.emit(str(exc))
            return False
        if had_pending:
            self.saved.emit("界面偏好已保存")
        return True

    def cancel(self) -> None:
        self._timer.stop()
