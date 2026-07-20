# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFrame,
    QLabel, QListWidget, QListWidgetItem, QStackedWidget,
    QStatusBar, QProgressBar, QPushButton, QComboBox, QMessageBox
)
from PySide6.QtCore import Qt
from .views.workstation import WorkstationView
from .views.tags_library import TagsView
from .views.settings import SettingsView
from .styles import get_main_style
from core.video_organizer_service import VideoOrganizerService, SettingsManager

class MainWindow(QMainWindow):
    def __init__(self, service: VideoOrganizerService):
        super().__init__()
        self.service = service
        self.settings = service.settings
        
        self.setWindowTitle("Video Organizer Pro (PySide6)")
        self.resize(1400, 900)

        self.setup_ui()
        self.refresh_style()
        
    def refresh_style(self):
        """刷新 UI 样式"""
        font_size = SettingsManager.get_setting(self.settings, "ui_preferences.font_size", 14)
        theme = SettingsManager.get_setting(self.settings, "ui_preferences.theme", "dark")
        self.setStyleSheet(get_main_style(font_size, theme))

    def setup_ui(self):
        """初始化主界面布局"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局：水平排列（侧边栏 + 内容区）
        self.main_layout = QHBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 1. 侧边栏
        self.sidebar_container = QFrame()
        self.sidebar_container.setObjectName("sidebar")
        self.sidebar_container.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(self.sidebar_container)
        sidebar_layout.setContentsMargins(10, 20, 10, 20)

        # 侧边栏标题
        title_label = QLabel("VIDEO ORGANIZER")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-weight: bold; font-size: 16px; margin-bottom: 20px;")
        sidebar_layout.addWidget(title_label)

        # 导航列表
        self.nav_list = QListWidget()
        self.nav_list.setFrameShape(QFrame.NoFrame)
        
        # 带有图标的导航项
        nav_items = [
            ("📂 工作台", 0),
            ("🏷️ 标签库", 1),
            ("⚙️ 系统设置", 2)
        ]
        for text, _ in nav_items:
            item = QListWidgetItem(text)
            self.nav_list.addItem(item)
            
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self.switch_page)
        
        sidebar_layout.addWidget(self.nav_list)
        sidebar_layout.addStretch()

        # 处理方案切换 (V6.0)
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

        # 侧边栏底部：主题切换与备份
        footer_layout = QVBoxLayout()
        
        self.theme_btn = QPushButton("🌓 切换主题")
        self.theme_btn.clicked.connect(self.toggle_theme)
        footer_layout.addWidget(self.theme_btn)
        
        self.backup_btn = QPushButton("💾 备份配置")
        self.backup_btn.clicked.connect(self.run_backup)
        footer_layout.addWidget(self.backup_btn)
        
        sidebar_layout.addLayout(footer_layout)

        self.main_layout.addWidget(self.sidebar_container)

        # 2. 右侧内容容器 (堆栈窗口)
        self.content_stack = QStackedWidget()
        self.content_stack.setContentsMargins(0, 0, 0, 0)
        
        # 实例化子页面
        self.workstation_page = WorkstationView(self.service)
        self.tags_page = TagsView(self.service)
        self.settings_page = SettingsView(self.service)
        
        # 信号联动
        self.settings_page.settings_applied.connect(self.on_settings_applied)
        
        # 添加到堆栈
        self.content_stack.addWidget(self.workstation_page)
        self.content_stack.addWidget(self.tags_page)
        self.content_stack.addWidget(self.settings_page)
        
        self.main_layout.addWidget(self.content_stack)

        # 3. 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("准备就绪")
        
        # 全局进度条
        self.global_progress = QProgressBar()
        self.global_progress.setMaximumWidth(200)
        self.global_progress.setVisible(False)
        self.status_bar.addPermanentWidget(self.global_progress)

        # 信号联动
        self.workstation_page.status_message.connect(self.status_bar.showMessage)
        self.workstation_page.progress_updated.connect(self.update_global_progress)

        # 初始化数据加载
        self.workstation_page.load_data()

    def refresh_presets(self):
        """从配置加载预设列表"""
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        
        global_settings = self.service.tag_config.get("global_settings", {})
        presets = global_settings.get("presets", {})
        
        if not presets:
            # 默认预设
            presets = {"默认方案": global_settings.get("export_schemes", {})}
            global_settings["presets"] = presets
            self.service.save_tag_config(self.service.tag_config)
            
        self.preset_combo.addItems(list(presets.keys()))
        
        # 选中当前匹配项 (如果可能)
        current_scheme = global_settings.get("export_schemes", {})
        for name, scheme in presets.items():
            if scheme == current_scheme:
                self.preset_combo.setCurrentText(name)
                break
        
        self.preset_combo.blockSignals(False)

    def on_preset_changed(self, preset_name):
        """处理预设切换"""
        if self.service.switch_preset(preset_name):
            self.status_bar.showMessage(f"已切换导出方案: {preset_name}", 3000)
            # 通知子页面刷新 (如有必要)
            self.on_settings_applied()

    def switch_page(self, index):
        """切换视图页面"""
        self.content_stack.setCurrentIndex(index)
        if index == 0:
            self.workstation_page.load_data()

    def update_global_progress(self, value):
        if value < 0:
            self.global_progress.setVisible(False)
        else:
            self.global_progress.setVisible(True)
            if value == 0:
                self.global_progress.setRange(0, 0) # 繁忙状态
            else:
                self.global_progress.setRange(0, 100)
                self.global_progress.setValue(value)

    def on_settings_applied(self):
        """当设置被应用时"""
        self.refresh_style()
        self.workstation_page.detail_panel.refresh_tag_completer()

    def toggle_theme(self):
        """切换深色/浅色模式"""
        current_theme = SettingsManager.get_setting(self.settings, "ui_preferences.theme", "dark")
        new_theme = "light" if current_theme == "dark" else "dark"
        SettingsManager.update_setting(self.settings, "ui_preferences.theme", new_theme)
        SettingsManager.save_settings(self.settings)
        self.refresh_style()

    def run_backup(self):
        """执行配置备份"""
        from PySide6.QtWidgets import QMessageBox
        path = self.service.backup_configuration()
        if path:
            QMessageBox.information(self, "备份成功", f"配置已成功备份至：\n{path}")
        else:
            QMessageBox.warning(self, "备份失败", "配置备份过程中出现错误。")
