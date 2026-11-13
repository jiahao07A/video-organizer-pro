# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFrame,
    QLabel, QListWidget, QListWidgetItem, QStackedWidget,
    QStatusBar, QProgressBar, QPushButton, QComboBox, QMessageBox, QDialog,
    QSystemTrayIcon, QMenu, QApplication
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QStyle
from PySide6.QtGui import QAction
from .views.workstation import WorkstationView

from .views.material_library import MaterialLibraryView
from .views.tags_library import TagsView
from .views.settings import SettingsView
from .views.media_watch_workspace import MediaWatchWorkspace
from .styles import get_main_style, normalize_theme
from core.video_organizer_service import VideoOrganizerService, SettingsManager


class MainWindow(QMainWindow):
    media_watch_status = Signal(dict)

    def __init__(self, service: VideoOrganizerService):
        super().__init__()
        self.service = service
        self.settings = service.settings
        from gui.services.update_controller import UpdateController
        self.update_controller = UpdateController(service, self)
        self.service.update_controller = self.update_controller
        self.update_controller.available.connect(self.show_update_notice)
        self.update_controller.prepared.connect(self._launch_update)
        self.update_controller.changed.connect(self._update_state_changed)
        self.update_controller.notice_dismissed.connect(self._dismiss_update_notice)
        self._update_preparing = False
        self._update_exiting = False
        self._update_notice = None
        self._allow_exit = False
        self._close_accepted = False
        self._monitor_paused = False
        self.media_watch_status.connect(self._refresh_media_watch_settings)
        from core.media_watch import MediaDirectoryMonitor
        self.media_monitor = MediaDirectoryMonitor(
            service, SettingsManager.get_setting(service.settings, "processing.media_watch", {}),
            on_status=self._media_watch_status_changed,
        )
        self.service.media_monitor = self.media_monitor
        
        self.setWindowTitle("Video Organizer Pro (PySide6)")
        self.resize(1400, 900)

        # 启动只恢复路径，扫盘延后到 UI 就绪后由工作台后台执行（避免启动卡死）
        self._pending_scope_scan = False
        try:
            if self.service.restore_work_scope_if_enabled(scan=False):
                self._pending_scope_scan = bool(self.service.get_work_scope_paths())
        except Exception:
            pass

        self.setup_ui()
        self._setup_tray()
        self._start_media_monitor()
        self.refresh_style()
        self.apply_sidebar_width()
        if self._pending_scope_scan:
            QTimer.singleShot(0, self._kick_background_scope_scan)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_controller.start_automatic_checks()

    def show_update_notice(self):
        from gui.widgets.software_update import SoftwareUpdatePanel
        if self._update_notice is not None:
            self._update_notice.show()
            self._update_notice.raise_()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("发现软件更新")
        dialog.setModal(False)
        dialog.resize(680, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(SoftwareUpdatePanel(self.update_controller, self.install_update, dialog))
        self._update_notice = dialog
        dialog.show()

    def _dismiss_update_notice(self):
        if self._update_notice:
            self._update_notice.hide()

    def install_update(self):
        from core.update_runtime import is_installed
        from gui.views.settings import _window_has_active_background_work
        from core.software_update import UpdateState
        if self.update_controller.state != UpdateState.READY or self.update_controller.busy:
            return
        if not is_installed():
            QMessageBox.information(self, "首次安装", "当前为源码或便携版。请先从官网下载并手动安装支持更新机制的安装版，启动后选择原管理数据目录。")
            return
        if _window_has_active_background_work(self):
            QMessageBox.warning(self, "任务尚未结束", "请先完成分析、扫描、重命名、导出、标签 AI 或数据维护任务，再安装更新。不会强制中断任务。")
            return
        try:
            if self.settings_page is not None and not self.settings_page.save_before_update():
                return
            SettingsManager.save_settings(self.service.settings, self.service.db)
        except Exception as exc:
            QMessageBox.warning(self, "无法保存设置", str(exc))
            return
        # 对话框与主窗口一起冻结，防止备份期间再次启动任务或改设置。
        self._update_preparing = True
        self.centralWidget().setEnabled(False)
        if self._update_notice:
            self._update_notice.setEnabled(False)
        self.update_controller.prepare()

    def _update_state_changed(self):
        from core.software_update import UpdateState
        if self._update_preparing and self.update_controller.state == UpdateState.FAILED:
            self._update_preparing = False
            self.centralWidget().setEnabled(True)
            if self._update_notice:
                self._update_notice.setEnabled(True)
            QMessageBox.warning(self, "更新准备失败", self.update_controller.message)

    def _launch_update(self, session):
        from core.update_runtime import launch_installer
        from gui.views.settings import _window_has_active_background_work
        try:
            if _window_has_active_background_work(self):
                raise RuntimeError("发现仍在运行的后台任务，已停止安装")
            c = self.update_controller
            launch_installer(c.release_directory() / c.release.filename, session, c.cache)
            self._update_preparing = False
            self._update_exiting = True
            if self._update_notice:
                self._update_notice.close()
            self.close()
        except Exception as exc:
            self.update_controller._fail(str(exc))

    def closeEvent(self, event):
        from gui.views.settings import _window_has_active_background_work
        if self._update_exiting:
            self._allow_exit = True
            self.media_monitor.stop(shutdown_analysis=True)
            self._close_accepted = True
            event.accept()
            return
        if self._update_preparing or self.media_monitor.analysis_running or _window_has_active_background_work(self):
            QMessageBox.warning(self, "任务尚未结束", "请等待后台任务完成后再退出；分析可先使用“取消分析”。")
            event.ignore()
            return
        if self.update_controller.busy:
            if self.update_controller.transfer:
                self.update_controller.transfer.cancel()
            event.ignore()
            self.status_bar.showMessage("正在取消更新请求，请稍后再次退出", 5000)
            return
        from gui.services.settings_controller import SettingsController
        if not all(controller.flush() for controller in self.findChildren(SettingsController)):
            event.ignore()
            return
        if self.settings_page is not None and not self.settings_page.prefs_controller.flush():
            event.ignore()
            return
        if self._update_notice:
            self._update_notice.close()
        if self._allow_exit or not self._tray_available:
            self.media_monitor.stop(shutdown_analysis=True)
            self._close_accepted = True
            event.accept()
        else:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Video Organizer Pro", "应用已最小化到托盘，素材监控仍在运行。",
                QSystemTrayIcon.Information, 3500,
            )

    def _setup_tray(self):
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        QApplication.instance().setQuitOnLastWindowClosed(not self._tray_available)
        if not self._tray_available:
            self.tray_icon = None
            return
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        self.tray_icon.setToolTip("Video Organizer Pro")
        self.tray_icon.activated.connect(lambda reason: self._show_window() if reason == QSystemTrayIcon.DoubleClick else None)
        menu = QMenu(self)
        open_action = QAction("打开主窗口", self)
        open_action.triggered.connect(self._show_window)
        self.monitor_action = QAction("暂停素材监控", self)
        self.monitor_action.triggered.connect(self._toggle_monitor)
        exit_action = QAction("彻底退出", self)
        exit_action.triggered.connect(self._exit_application)
        menu.addAction(open_action)
        menu.addAction(self.monitor_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

    def _start_media_monitor(self):
        if any(item["enabled"] for item in self.media_monitor.config["directories"]):
            self.media_monitor.start()

    def _reconfigure_media_monitor(self):
        config = SettingsManager.get_setting(self.service.settings, "processing.media_watch", {}) or {}
        self.media_monitor.reconfigure(config)
        self._monitor_paused = False
        if hasattr(self, "monitor_action"):
            self.monitor_action.setText("暂停素材监控")
        if self.settings_page and self.settings_page.media_watch_page:
            self.settings_page.media_watch_page.refresh()
        if self.library_page:
            self.library_page.filter_panel.refresh_watch_tasks()

    def _media_watch_status_changed(self, _status):
        self.media_watch_status.emit(_status)

    def _refresh_media_watch_settings(self, _status=None):
        # 工作区自身按秒刷新；文件事件不得重载未保存的高级设置。
        if _status and _status.get('notify') and self.tray_icon:
            self.tray_icon.showMessage('素材监控', _status.get('error') or '监控批次处理完成，可在素材监控中查看结果。')

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _toggle_monitor(self):
        self._monitor_paused = not self._monitor_paused
        if self._monitor_paused:
            self.media_monitor.pause()
            self.monitor_action.setText("恢复素材监控")
        else:
            self.media_monitor.resume()
            self.monitor_action.setText("暂停素材监控")

    def _exit_application(self):
        if self.update_controller.busy or self._update_preparing:
            self.status_bar.showMessage("更新处理中，请等待完成后再退出", 5000)
            return
        from gui.views.settings import _window_has_active_background_work
        if _window_has_active_background_work(self):
            QMessageBox.warning(self, "任务尚未结束", "请等待后台任务完成后再退出；分析可先使用“取消分析”。")
            return
        self._allow_exit = True
        self._close_accepted = False
        self.close()
        if self._close_accepted:
            QApplication.instance().quit()
        else:
            self._allow_exit = False

    def _kick_background_scope_scan(self):
        page = getattr(self, "workstation_page", None)
        if page is not None and hasattr(page, "background_scan_work_scope"):
            page.background_scan_work_scope(reason="启动恢复工作范围")

    def refresh_style(self):
        """刷新主窗 QSS，并广播主题到已创建的子视图。"""
        font_size = SettingsManager.get_setting(self.settings, "ui_preferences.font_size", 14)
        raw_theme = SettingsManager.get_setting(self.settings, "ui_preferences.theme", "dark")
        theme = normalize_theme(raw_theme)
        if raw_theme != theme:
            SettingsManager.update_setting(self.settings, "ui_preferences.theme", theme)
            SettingsManager.save_settings(self.settings, self.service.db)
        self.setStyleSheet(get_main_style(font_size, theme))
        self._propagate_theme(theme)
        self.apply_sidebar_width()

    def apply_sidebar_width(self):
        w = int(SettingsManager.get_setting(self.settings, "ui_preferences.sidebar_width", 220) or 220)
        w = max(160, min(360, w))
        if hasattr(self, "sidebar_container"):
            self.sidebar_container.setFixedWidth(w)

    def _propagate_theme(self, theme: str):
        theme = normalize_theme(theme)
        for page_name in ("workstation_page", "library_page", "tags_page"):
            page = getattr(self, page_name, None)
            if page is not None and hasattr(page, "apply_theme"):
                page.apply_theme(theme)

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        self.main_layout = QHBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.sidebar_container = QFrame()
        self.sidebar_container.setObjectName("sidebar")
        self.sidebar_container.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(self.sidebar_container)
        sidebar_layout.setContentsMargins(10, 20, 10, 20)

        title_label = QLabel("VIDEO ORGANIZER")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-weight: bold; font-size: 16px; margin-bottom: 20px;")
        sidebar_layout.addWidget(title_label)

        self.nav_list = QListWidget()
        self.nav_list.setFrameShape(QFrame.NoFrame)
        
        # 0 工作台 / 1 素材库 / 2 标签库 / 3 系统设置
        nav_items = [
            ("📂 工作台", 0),
            ("📚 素材库", 1),
            ("👁 素材监控", 4),
            ("🏷️ 标签库", 2),
            ("⚙️ 系统设置", 3),
        ]
        for text, page_id in nav_items:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, page_id)
            self.nav_list.addItem(item)
            
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(lambda row: self.switch_page(self.nav_list.item(row).data(Qt.UserRole)) if row >= 0 else None)
        
        sidebar_layout.addWidget(self.nav_list)
        sidebar_layout.addStretch()

        preset_layout = QVBoxLayout()
        preset_label = QLabel("🎬 导出方案预设:")
        preset_label.setStyleSheet("font-size: 12px; color: #888;")
        self.preset_combo = QComboBox()
        self.refresh_presets()
        self.preset_combo.currentTextChanged.connect(self.on_preset_changed)
        preset_layout.addWidget(preset_label)
        preset_layout.addWidget(self.preset_combo)
        sidebar_layout.addLayout(preset_layout)
        sidebar_layout.addSpacing(6)

        footer_layout = QVBoxLayout()
        self.feedback_btn = QPushButton("问题反馈 / 发布 Issue")
        self.feedback_btn.clicked.connect(self.show_diagnostic_feedback)
        footer_layout.addWidget(self.feedback_btn)
        
        self.theme_btn = QPushButton("🌓 切换主题")
        self.theme_btn.clicked.connect(self.toggle_theme)
        footer_layout.addWidget(self.theme_btn)
        
        self.backup_btn = QPushButton("💾 备份管理数据")
        self.backup_btn.clicked.connect(self.run_backup)
        footer_layout.addWidget(self.backup_btn)
        
        sidebar_layout.addLayout(footer_layout)

        self.main_layout.addWidget(self.sidebar_container)

        self.content_stack = QStackedWidget()
        self.content_stack.setContentsMargins(0, 0, 0, 0)
        self._page_placeholders = []
        self.workstation_page = None
        self.library_page = None
        self.tags_page = None
        self.settings_page = None
        self.media_watch_page = None
        for _ in range(5):
            placeholder = QWidget()
            self._page_placeholders.append(placeholder)
            self.content_stack.addWidget(placeholder)
        self.main_layout.addWidget(self.content_stack)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("准备就绪")
        self._diagnostic_warning = ''
        self._diagnostic_timer = QTimer(self)
        self._diagnostic_timer.setInterval(5000)
        self._diagnostic_timer.timeout.connect(self._check_diagnostic_health)
        self._diagnostic_timer.start()

        self.global_progress = QProgressBar()
        self.global_progress.setMaximumWidth(200)
        self.global_progress.setVisible(False)
        self.status_bar.addPermanentWidget(self.global_progress)

        # 只创建默认页；其余页面在首次进入时创建并缓存。
        self._ensure_page(0)
        self.content_stack.setCurrentWidget(self.workstation_page)
        self.workstation_page.load_data()
        expanded = SettingsManager.get_setting(
            self.settings, "ui_preferences.detail_panel_expanded", True
        )
        self.workstation_page.detail_panel.setVisible(bool(expanded))

    def _check_diagnostic_health(self):
        from core.diagnostic_logging import get_runtime
        runtime = get_runtime()
        if runtime and runtime.warning and runtime.warning != self._diagnostic_warning:
            self._diagnostic_warning = runtime.warning
            self.feedback_btn.setToolTip(runtime.warning)
            self.status_bar.showMessage(runtime.warning)

    def show_diagnostic_feedback(self):
        from gui.widgets.diagnostic_feedback import DiagnosticFeedbackDialog
        dialog = getattr(self, '_feedback_dialog', None)
        if dialog is None:
            dialog = DiagnosticFeedbackDialog(self.service, self)
            self._feedback_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _ensure_page(self, index: int):
        """首次进入时创建页面，并保留页面实例供后续切换复用。"""
        attrs = ("workstation_page", "library_page", "tags_page", "settings_page", "media_watch_page")
        if index < 0 or index >= len(attrs):
            return None
        attr = attrs[index]
        page = getattr(self, attr, None)
        if page is not None:
            return page

        factories = (WorkstationView, MaterialLibraryView, TagsView, SettingsView, MediaWatchWorkspace)
        page = factories[index](self.service)
        placeholder = self._page_placeholders[index]
        self.content_stack.removeWidget(placeholder)
        self.content_stack.insertWidget(index, page)
        self._page_placeholders[index] = None
        if placeholder is not None:
            placeholder.deleteLater()
        setattr(self, attr, page)

        if index == 0:
            page.status_message.connect(self.status_bar.showMessage)
            page.progress_updated.connect(self.update_global_progress)
        elif index == 1:
            page.status_message.connect(self.status_bar.showMessage)
            page.work_scope_changed.connect(self.workstation_page.load_data)
        elif index == 3:
            page.settings_applied.connect(self.on_settings_applied)
            page.media_watch_changed.connect(self._reconfigure_media_monitor)
            page.ui_preferences_applied.connect(self.apply_ui_preferences)
            page.interface_layout_reset.connect(self.reset_open_list_column_widths)
        elif index == 4:
            page.changed.connect(self._reconfigure_media_monitor)
            page.open_advanced.connect(self._open_watch_advanced)
            page.work_scope_changed.connect(self.workstation_page.load_data)
            page.status_message.connect(self.status_bar.showMessage)

        if hasattr(page, "apply_default_view"):
            page.apply_default_view()
        if hasattr(page, "detail_panel"):
            expanded = SettingsManager.get_setting(
                self.settings, "ui_preferences.detail_panel_expanded", True
            )
            page.detail_panel.setVisible(bool(expanded))
        if hasattr(page, "apply_theme"):
            page.apply_theme(normalize_theme(
                SettingsManager.get_setting(self.settings, "ui_preferences.theme", "dark")
            ))
        return page

    def _open_watch_advanced(self, task_id=''):
        settings = self._ensure_page(3)
        self.nav_list.setCurrentRow(4)
        settings.tabs.setCurrentWidget(settings.media_watch_page)
        settings.media_watch_page.refresh()
        settings.media_watch_page.target.setCurrentIndex(max(0, settings.media_watch_page.target.findData(task_id)))

    def refresh_presets(self):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        
        global_settings = self.service.tag_config.get("global_settings", {})
        presets = global_settings.get("presets", {})
        
        if not presets:
            presets = {"默认方案": global_settings.get("export_schemes", {})}
            global_settings["presets"] = presets
            self.service.save_tag_config(self.service.tag_config)
            
        self.preset_combo.addItems(list(presets.keys()))
        
        current_scheme = global_settings.get("export_schemes", {})
        for name, scheme in presets.items():
            if scheme == current_scheme:
                self.preset_combo.setCurrentText(name)
                break
        
        self.preset_combo.blockSignals(False)

    def on_preset_changed(self, preset_name):
        if self.service.switch_preset(preset_name):
            self.status_bar.showMessage(f"已切换导出方案: {preset_name}", 3000)
            self.on_settings_applied()

    def switch_page(self, index):
        page = self._ensure_page(index)
        if page is None:
            return
        self.content_stack.setCurrentWidget(page)
        if index == 0:
            page.load_data()
        elif index == 1:
            page.load_data()

    def update_global_progress(self, value):
        if value < 0:
            self.global_progress.setVisible(False)
        else:
            self.global_progress.setVisible(True)
            if value == 0:
                self.global_progress.setRange(0, 0)
            else:
                self.global_progress.setRange(0, 100)
                self.global_progress.setValue(value)

    def on_settings_applied(self):
        self.apply_ui_preferences()
        if hasattr(self.workstation_page, "refresh_analysis_side_effect_summary"):
            self.workstation_page.refresh_analysis_side_effect_summary()
        # 敏感设置显式保存时才重建 AI 客户端。
        if hasattr(self.service, "reload_ai_from_settings"):
            try:
                self.service.reload_ai_from_settings()
            except Exception:
                pass
        if hasattr(self.workstation_page, "detail_panel"):
            self.workstation_page.detail_panel.refresh_tag_completer()
        if hasattr(self.library_page, "detail_panel"):
            self.library_page.detail_panel.refresh_tag_completer()

    def apply_ui_preferences(self):
        """应用成功保存的普通偏好，不修改模型连接或未保存敏感配置。"""
        self.refresh_style()
        if hasattr(self.workstation_page, "apply_default_view"):
            self.workstation_page.apply_default_view()
        if hasattr(self.library_page, "apply_default_view"):
            self.library_page.apply_default_view()
        expanded = SettingsManager.get_setting(
            self.settings, "ui_preferences.detail_panel_expanded", True
        )
        for page in (self.workstation_page, self.library_page):
            if hasattr(page, "detail_panel"):
                page.detail_panel.setVisible(bool(expanded))

    def reset_open_list_column_widths(self):
        """让已创建的列表页立即采用重置后的默认列宽。"""
        for page in (self.workstation_page, self.library_page):
            apply_widths = getattr(page, "apply_default_column_widths", None)
            if callable(apply_widths):
                apply_widths()

    def toggle_theme(self):
        current_theme = normalize_theme(
            SettingsManager.get_setting(self.settings, "ui_preferences.theme", "dark")
        )
        new_theme = "light" if current_theme == "dark" else "dark"
        SettingsManager.update_setting(self.settings, "ui_preferences.theme", new_theme)
        SettingsManager.save_settings(self.settings, self.service.db)
        self.refresh_style()

    def run_backup(self):
        path = self.service.backup_configuration()
        if path:
            QMessageBox.information(self, "备份成功", f"管理数据已成功备份至：\n{path}")
        else:
            QMessageBox.warning(self, "备份失败", "管理数据备份过程中出现错误。")
