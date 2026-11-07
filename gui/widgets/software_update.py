"""软件更新设置页与非阻塞提醒共用的操作面板。"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QPlainTextEdit, QProgressBar, QFileDialog,
)

from core.app_version import APP_VERSION, RELEASES_URL
from core.software_update import UpdateState


class SoftwareUpdatePanel(QWidget):
    def __init__(self, controller, install, parent=None):
        super().__init__(parent)
        self.setObjectName("software_update_panel")
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"当前版本：{APP_VERSION} · Windows x64"))
        self.auto = QCheckBox("自动检查更新（每天最多一次，不自动下载）")
        self.preview = QCheckBox("接收测试版本（可能不稳定）")
        self.auto.setChecked(controller.preferences["auto_check"])
        self.preview.setChecked(controller.preferences["preview"])
        self.auto.toggled.connect(lambda value: controller.set_preference("auto_check", value))
        self.preview.toggled.connect(lambda value: controller.set_preference("preview", value))
        layout.addWidget(self.auto)
        layout.addWidget(self.preview)
        self.last = QLabel()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(130)
        self.progress = QProgressBar()
        layout.addWidget(self.last)
        layout.addWidget(self.status)
        layout.addWidget(self.details)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.check = QPushButton("检查更新")
        self.check.clicked.connect(lambda: controller.check(manual=True))
        self.download = QPushButton("下载更新")
        self.download.clicked.connect(controller.download)
        self.cancel = QPushButton("取消下载")
        self.cancel.clicked.connect(controller.cancel_download)
        self.install = QPushButton("安装并重启")
        self.install.clicked.connect(install)
        for button in (self.check, self.download, self.cancel, self.install):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.later = QPushButton("稍后提醒 / 稍后安装")
        self.later.clicked.connect(controller.snooze)
        self.skip = QPushButton("跳过此版本")
        self.skip.clicked.connect(controller.skip)
        self.offline = QPushButton("导入离线更新包")
        self.offline.clicked.connect(self.import_offline)
        for button in (self.later, self.skip, self.offline):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for label in ("查看更新记录", "打开官方下载页"):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
            row.addWidget(button)
        recovery = QPushButton("更新失败与恢复指引")
        recovery.clicked.connect(self.show_recovery)
        row.addWidget(recovery)
        layout.addLayout(row)
        self.help = QLabel("首次接入需手动安装；ZIP 旧版请安装后选择原管理数据目录。更新不会移动原始视频。")
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        layout.addStretch()
        controller.changed.connect(self.refresh)
        self.refresh()

    def import_offline(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 update.json（同目录须有安装包和 update.json.sig）", "", "更新清单 (update.json)")
        if path:
            self.controller.import_offline(Path(path))

    def show_recovery(self):
        from core.software_update import UpdateSession
        try:
            session = UpdateSession.load(self.controller.cache)
            backup = session.backup_path if session else "暂无更新前备份"
            previous = session.previous_version if session else "请在官方下载页选择"
        except Exception as exc:
            backup, previous = str(exc), "请在官方下载页选择"
        self.help.setText(
            f"更新前备份：{backup}\n旧版本：{previous}\n"
            "1. 保留当前数据，退出所有应用实例。2. 从官网下载所需安装版本。"
            "3. 在兼容版本的管理数据备份/恢复入口选择更新前快照；恢复前会保护当前数据，并需要你确认。"
            "恢复会失去更新后新增记录；旧程序不能直接打开更高版本数据库。"
            "若程序无法启动，请保留整个数据目录及备份，按随安装包提供的《更新与恢复》文档操作，勿删除数据库。"
        )

    def refresh(self):
        c = self.controller
        self.status.setText(c.message)
        if c.mirror_used:
            self.status.setText(c.message + "\n本次使用国内镜像 gh-proxy.org；安装前仍验证官方签名和摘要。")
        timestamp = c.preferences["last_check"]
        self.last.setText("上次检查：" + (datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M") if timestamp else "尚未检查"))
        for checkbox, key in ((self.auto, "auto_check"), (self.preview, "preview")):
            checkbox.blockSignals(True)
            checkbox.setChecked(c.preferences[key])
            checkbox.blockSignals(False)
            checkbox.setEnabled(not c.busy)
        self.check.setEnabled(not c.busy)
        self.offline.setEnabled(not c.busy)
        self.download.setEnabled(not c.busy and bool(c.release) and c.state != UpdateState.READY)
        self.install.setEnabled(not c.busy and c.state == UpdateState.READY)
        self.cancel.setEnabled(c.state == UpdateState.DOWNLOADING and c.transfer is not None)
        self.skip.setEnabled(not c.busy and bool(c.release))
        self.later.setEnabled(not c.busy)
        if c.release:
            r = c.release
            self.details.setPlainText(f"版本：{r.version} · {'测试版' if r.channel == 'preview' else '正式版'}\n发布日期：{r.published_at}\n下载大小：{r.size / 1024**2:.1f} MB\n\n{r.notes}")
        else:
            self.details.clear()
        self.progress.setVisible(c.state == UpdateState.DOWNLOADING)
        self.progress.setRange(0, 100)
        if c.release:
            self.progress.setValue(min(100, int(c.received * 100 / c.release.size)))
            self.progress.setFormat(f"{c.received / 1024**2:.1f} / {c.release.size / 1024**2:.1f} MB · %p%")
