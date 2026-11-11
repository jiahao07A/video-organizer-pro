"""诊断包导出和由用户提交的网页反馈。"""
import copy
from pathlib import Path
import threading

from PySide6.QtCore import QUrl, Qt, QEvent, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
    QScrollArea, QWidget, QLayout, QBoxLayout)
from core.diagnostic_bundle import build_bundle, ISSUE_URL
from gui.workers.io_worker import run_io_job


class DiagnosticFeedbackDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.bundle_result = None
        self._result_form = None
        self._pending_form = None
        self.cancel_event = threading.Event()
        self.setWindowTitle('问题反馈 / 发布 Issue')
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll)
        privacy = QLabel('自动收集脱敏日志、技术环境与失败摘要；不包含素材、原始配置或数据库。\n生成后将打开 GitHub，请将 ZIP 拖入网页，检查内容后自行提交。')
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        form = QFormLayout()
        self.summary = QLineEdit()
        self.summary.setPlaceholderText('例如：分析完成后导出失败')
        self.summary.textChanged.connect(self.form_changed)
        self.inputs = {}
        form.addRow('问题摘要', self.summary)
        for key, label in [('steps', '复现步骤'), ('expected', '期望结果'), ('actual', '实际结果')]:
            edit = QPlainTextEdit()
            edit.setTabChangesFocus(True)
            edit.textChanged.connect(self.form_changed)
            edit.setMaximumHeight(85)
            self.inputs[key] = edit
            form.addRow(label, edit)
        self.period = QComboBox()
        self.period.currentIndexChanged.connect(self.form_changed)
        for label, value in [('本次及最近异常会话', 'session'), ('最近24小时', '24h'), ('最近7天', '7d')]:
            self.period.addItem(label, value)
        form.addRow('日志范围', self.period)
        layout.addLayout(form)
        self.status = QLabel('尚未生成诊断包')
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setTabChangesFocus(True)
        self.preview.setMinimumHeight(100)
        layout.addWidget(self.preview, 1)
        row = QHBoxLayout()
        self.generate = QPushButton('生成诊断包并打开 Issue')
        self.generate.clicked.connect(self.start)
        self.cancel = QPushButton('取消生成')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.request_cancel)
        row.addWidget(self.generate)
        row.addWidget(self.cancel)
        outer.addLayout(row)
        self.generate_row = row
        row = QHBoxLayout()
        self.actions = []
        for label, callback in [('打开文件位置', self.reveal), ('复制反馈正文', self.copy_body), ('打开 Issue 页面', self.open_issue)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            button.setEnabled(False)
            self.actions.append(button)
            row.addWidget(button)
        outer.addLayout(row)
        self.action_row = row
        for button in [self.generate, self.cancel, *self.actions]:
            button.setAutoDefault(False)
        self._scroll_fields = [self.summary, *self.inputs.values(), self.period, self.preview]
        for widget in self._scroll_fields:
            widget.installEventFilter(self)
        available = self.screen().availableGeometry()
        self.resize(min(650, max(240, available.width() - 24)),
                    min(680, max(240, available.height() - 24)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'action_row'):
            direction = QBoxLayout.TopToBottom if self.width() < 560 else QBoxLayout.LeftToRight
            self.generate_row.setDirection(direction)
            self.action_row.setDirection(direction)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.FocusIn and watched in getattr(self, '_scroll_fields', []):
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(watched, 0, 8))
        return super().eventFilter(watched, event)

    def form_snapshot(self):
        return ({'summary': self.summary.text(), **{k: v.toPlainText() for k, v in self.inputs.items()}},
                self.period.currentData())

    def result_is_current(self):
        return self.bundle_result is not None and self.form_snapshot() == self._result_form

    def restore_actions(self):
        if not hasattr(self, 'actions'):
            return
        busy = self.cancel.isEnabled()
        self.actions[0].setEnabled(bool(self.bundle_result) and not busy)
        for button in self.actions[1:]:
            button.setEnabled(self.result_is_current() and not busy)

    def form_changed(self, *_):
        if self.bundle_result and hasattr(self, 'actions'):
            self.restore_actions()
            if not self.result_is_current():
                self.status.setText('内容或日志范围已修改，请重新生成诊断包后复制正文或打开 Issue。上次诊断包仍可打开文件位置；下方为上次生成的预览。')
            else:
                self.status.setText('当前填写内容与上次诊断包一致，可继续反馈；尚未提交 Issue。')

    def request_cancel(self):
        self.cancel_event.set()
        self.cancel.setEnabled(False)
        self.status.setText('正在取消，请等待后台整理结束…')

    def start(self):
        if getattr(self, '_io_worker', None) is not None:
            return
        path, _ = QFileDialog.getSaveFileName(self, '保存脱敏诊断包', 'video-organizer-diagnostics.zip', 'ZIP (*.zip)')
        if not path:
            return
        fields = {'summary': self.summary.text(), **{k: v.toPlainText() for k, v in self.inputs.items()}}
        settings = copy.deepcopy(self.service.settings)
        database = self.service._database_path
        period = self.period.currentData()
        self._pending_form = (fields, period)
        self.cancel_event.clear()
        self.generate.setEnabled(False)
        self.cancel.setEnabled(True)
        self.progress.show()
        for widget in [self.summary, self.period, *self.inputs.values()]:
            widget.setEnabled(False)
        for button in self.actions:
            button.setEnabled(False)
        self.status.setText('正在后台整理和脱敏，可取消…')
        run_io_job(self, fn=lambda: build_bundle(path, settings=settings, database=database, period=period, fields=fields, cancel=self.cancel_event),
                   on_ok=self.completed, on_fail=self.failed)

    def completed(self, result):
        self.bundle_result = result
        self._result_form = self._pending_form or self.form_snapshot()
        self._pending_form = None
        self.progress.hide()
        for widget in [self.summary, self.period, *self.inputs.values()]:
            widget.setEnabled(True)
        self.generate.setEnabled(True)
        self.cancel.setEnabled(False)
        for button in self.actions:
            button.setEnabled(True)
        self.preview.setPlainText(result['preview'])
        self.status.setText(f"诊断包已生成：{result['size'] / 1024:.1f} KB；缺项/裁剪说明 {len(result['omissions'])} 项。\n{result['path']}\n请将 ZIP 拖入网页后自行提交；目前尚未提交 Issue。")
        if self.cancel_event.is_set():
            self.status.setText(self.status.text() + '\n取消请求到达时文件已生成，未自动打开浏览器。')
        else:
            self.open_issue()

    def failed(self, message):
        self._pending_form = None
        self.progress.hide()
        for widget in [self.summary, self.period, *self.inputs.values()]:
            widget.setEnabled(True)
        self.generate.setEnabled(True)
        self.cancel.setEnabled(False)
        self.restore_actions()
        text = '已取消本次生成。' if self.cancel_event.is_set() else '本次诊断包未生成：' + message
        if self.bundle_result:
            text += '\n上次诊断包仍保留。'
            if not self.result_is_current():
                text += '填写内容已修改，请重新生成后复制正文或打开 Issue；仍可打开上次文件位置。'
        self.status.setText(text)

    def reveal(self):
        if self.bundle_result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.bundle_result['path']).parent)))

    def copy_body(self):
        if self.result_is_current():
            QApplication.clipboard().setText(self.bundle_result['body'])
            self.status.setText('反馈正文已复制。请粘贴到 Issue 页面，并拖入 ZIP 附件后自行提交。')

    def open_issue(self):
        if not self.result_is_current():
            return
        if not QDesktopServices.openUrl(QUrl(self.bundle_result['url'])):
            self.status.setText(self.status.text() + '\n浏览器未能打开；诊断包已保留。请复制正文并访问：' + ISSUE_URL)
            self.preview.setPlainText(self.bundle_result['preview'] + '\nIssue 页面：' + ISSUE_URL)
        else:
            self.status.setText(self.status.text() + '\n已请求浏览器打开 Issue 页面；尚未提交。')

    def reject(self):
        if getattr(self, '_io_worker', None) is not None:
            self.request_cancel()
            self.status.setText('正在取消，请等待后台生成结束后关闭。')
            return
        super().reject()
