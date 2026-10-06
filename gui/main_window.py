# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFrame,
    QLabel, QListWidget, QListWidgetItem, QStackedWidget,
    QStatusBar, QProgressBar, QPushButton, QComboBox, QMessageBox
)
from PySide6.QtCore import Qt, QTimer
from .views.workstation import WorkstationView
from .views.material_library import MaterialLibraryView
from .views.tags_library import TagsView
from .views.settings import SettingsView
from .styles import get_main_style, normalize_theme
from core.video_organizer_service import VideoOrganizerService, SettingsManager

class MainWindow(QMainWindow):
    def __init__(self, service: VideoOrganizerService):
        super().__init__()
        self.service = service
        self.settings = service.settings
        
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
        self.refresh_style()
        self.apply_sidebar_width()
        if self._pending_scope_scan:
            QTimer.singleShot(0, self._kick_background_scope_scan)

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
            SettingsManager.save_settings(self.settings)
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
            ("🏷️ 标签库", 2),
            ("⚙️ 系统设置", 3),
        ]
        for text, _ in nav_items:
            item = QListWidgetItem(text)
            self.nav_list.addItem(item)
            
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self.switch_page)
        
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
        sidebar_layout.addSpacing(20)

        footer_layout = QVBoxLayout()
        
        self.theme_btn = QPushButton("🌓 切换主题")
        self.theme_btn.clicked.connect(self.toggle_theme)
        footer_layout.addWidget(self.theme_btn)
        
        self.backup_btn = QPushButton("💾 备份配置")
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
        for _ in range(4):
            placeholder = QWidget()
            self._page_placeholders.append(placeholder)
            self.content_stack.addWidget(placeholder)
        self.main_layout.addWidget(self.content_stack)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("准备就绪")

        self.global_progress = QProgressBar()
        self.global_progress.setMaximumWidth(200)
        self.global_progress.setVisible(False)
        self.status_bar.addPermanentWidget(self.global_progress)

        # 只创建默认页；其余页面在首次进入时创建并缓存。
        self._ensure_page(0)
        self.workstation_page.load_data()
        expanded = SettingsManager.get_setting(
            self.settings, "ui_preferences.detail_panel_expanded", True
        )
        self.workstation_page.detail_panel.setVisible(bool(expanded))

    def _ensure_page(self, index: int):
        """首次进入时创建页面，并保留页面实例供后续切换复用。"""
        attrs = ("workstation_page", "library_page", "tags_page", "settings_page")
        if index < 0 or index >= len(attrs):
            return None
        attr = attrs[index]
        page = getattr(self, attr, None)
        if page is not None:
            return page

        factories = (WorkstationView, MaterialLibraryView, TagsView, SettingsView)
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
        self.refresh_style()
        # 确保 AI 客户端与最新 settings 一致
        if hasattr(self.service, "reload_ai_from_settings"):
            try:
                self.service.reload_ai_from_settings()
            except Exception:
                pass
        if hasattr(self.workstation_page, "detail_panel"):
            self.workstation_page.detail_panel.refresh_tag_completer()
        if hasattr(self.library_page, "detail_panel"):
            self.library_page.detail_panel.refresh_tag_completer()
        if hasattr(self.workstation_page, "apply_default_view"):
            self.workstation_page.apply_default_view()
        if hasattr(self.library_page, "apply_default_view"):
            self.library_page.apply_default_view()
        # 详情默认展开/收起
        expanded = SettingsManager.get_setting(
            self.settings, "ui_preferences.detail_panel_expanded", True
        )
        for page in (self.workstation_page, self.library_page):
            if hasattr(page, "detail_panel"):
                page.detail_panel.setVisible(bool(expanded))

    def toggle_theme(self):
        current_theme = normalize_theme(
            SettingsManager.get_setting(self.settings, "ui_preferences.theme", "dark")
        )
        new_theme = "light" if current_theme == "dark" else "dark"
        SettingsManager.update_setting(self.settings, "ui_preferences.theme", new_theme)
        SettingsManager.save_settings(self.settings)
        self.refresh_style()

    def run_backup(self):
        path = self.service.backup_configuration()
        if path:
            QMessageBox.information(self, "备份成功", f"配置已成功备份至：\n{path}")
        else:
            QMessageBox.warning(self, "备份失败", "配置备份过程中出现错误。")