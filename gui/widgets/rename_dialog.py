# -*- coding: utf-8 -*-
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QAbstractItemView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from core.video_organizer_service import VideoOrganizerService, FileManager, SettingsManager
from gui.styles import get_theme_colors, normalize_theme


class BatchRenameDialog(QDialog):
    """批量重命名预览对话框，支持正则表达式预览（主题可读对比度）。"""

    def __init__(self, service: VideoOrganizerService, selected_videos, parent=None):
        super().__init__(parent)
        self.service = service
        self.selected_videos = selected_videos
        self.setWindowTitle("高级批量重命名 (正则预览)")
        self.resize(1000, 700)
        self._theme = normalize_theme(
            SettingsManager.get_setting(service.settings, "ui_preferences.theme", "dark")
        )
        self._colors = get_theme_colors(self._theme)
        self.setup_ui()
        self._apply_theme_styles()
        self.update_preview()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(14, 14, 14, 14)

        # 模式配置区（颜色由 _apply_theme_styles 统一设置，禁止硬编码深色底）
        self.config_frame = QFrame()
        self.config_frame.setObjectName("rename_config_frame")
        config_layout = QVBoxLayout(self.config_frame)
        config_layout.setSpacing(10)

        pattern_layout = QHBoxLayout()
        self.pattern_label = QLabel("重命名模式:")
        pattern_layout.addWidget(self.pattern_label)
        self.pattern_input = QLineEdit()
        global_settings = self.service.tag_config.get("global_settings", {})
        default_pattern = global_settings.get("export_schemes", {}).get(
            "filename_pattern", "{category}-{tags}-{summary}-{original_name}"
        )
        self.pattern_input.setText(default_pattern)
        self.pattern_input.textChanged.connect(self.update_preview)
        pattern_layout.addWidget(self.pattern_input)
        config_layout.addLayout(pattern_layout)

        regex_layout = QHBoxLayout()
        self.regex_find_label = QLabel("正则查找:")
        regex_layout.addWidget(self.regex_find_label)
        self.regex_find = QLineEdit()
        self.regex_find.setPlaceholderText("例如: ^IMG_")
        self.regex_find.textChanged.connect(self.update_preview)
        regex_layout.addWidget(self.regex_find)

        self.regex_replace_label = QLabel("替换为:")
        regex_layout.addWidget(self.regex_replace_label)
        self.regex_replace = QLineEdit()
        self.regex_replace.setPlaceholderText("例如: CLIP_")
        self.regex_replace.textChanged.connect(self.update_preview)
        regex_layout.addWidget(self.regex_replace)
        config_layout.addLayout(regex_layout)

        self.help_label = QLabel("变量支持: {category}, {tags}, {summary}, {original_name}")
        config_layout.addWidget(self.help_label)

        layout.addWidget(self.config_frame)

        self.preview_table = QTableWidget()
        self.preview_table.setObjectName("rename_preview_table")
        self.preview_table.setColumnCount(3)
        self.preview_table.setHorizontalHeaderLabels(["当前文件名", "→", "新文件名预览"])
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.preview_table.setColumnWidth(1, 40)
        self.preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.verticalHeader().setVisible(True)
        layout.addWidget(self.preview_table, 1)

        btn_layout = QHBoxLayout()
        self.status_label = QLabel("")
        btn_layout.addWidget(self.status_label)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)

        self.apply_btn = QPushButton("执行重命名")
        self.apply_btn.setObjectName("primary_button")
        self.apply_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.apply_btn)
        layout.addLayout(btn_layout)

    def _apply_theme_styles(self):
        c = self._colors
        # 整窗：背景与正文
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {c["bg"]};
                color: {c["text"]};
            }}
            QLabel {{
                color: {c["text"]};
                background: transparent;
            }}
            QLineEdit {{
                background-color: {c["input_bg"]};
                color: {c["input_text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
                padding: 6px 8px;
                selection-background-color: {c["accent"]};
            }}
            QLineEdit:focus {{
                border: 1px solid {c["accent"]};
            }}
            QFrame#rename_config_frame {{
                background-color: {c["panel_bg"]};
                border: 1px solid {c["panel_border"]};
                border-radius: 8px;
                padding: 8px;
            }}
            QTableWidget#rename_preview_table {{
                background-color: {c["item_bg"]};
                color: {c["text"]};
                gridline-color: {c["border"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
            }}
            QTableWidget#rename_preview_table::item {{
                padding: 4px;
                color: {c["text"]};
            }}
            QHeaderView::section {{
                background-color: {c["panel_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                padding: 6px;
                font-weight: bold;
            }}
            QPushButton {{
                background-color: {c["item_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background-color: {c["accent_hover"]};
                color: #ffffff;
                border-color: {c["accent_hover"]};
            }}
            QPushButton#primary_button {{
                background-color: {c["accent"]};
                color: #ffffff;
                border: none;
                font-weight: bold;
                padding: 8px 20px;
            }}
            QPushButton#primary_button:hover {{
                background-color: {c["accent_hover"]};
            }}
        """)
        self.help_label.setStyleSheet(f"color: {c['muted']}; font-size: 12px; background: transparent;")

    def _changed_name_color(self) -> QColor:
        # 浅色主题用深绿；深色主题用亮绿，保证对比度
        if self._theme == "light":
            return QColor("#0d7a3f")
        return QColor("#73d13d")

    def _unchanged_name_color(self) -> QColor:
        return QColor(self._colors["secondary_text"])

    def get_preview_filename(self, item, pattern, find_regex, replace_str):
        category = item.get("category", "Other")
        tags = item.get("tags", [])
        tags_str = "_".join(tags)
        summary_safe = FileManager.sanitize_filename(item.get("summary", ""), max_len=50)

        current_fn = item.get("filename", "")
        raw_parts = current_fn.split("-")
        original_suffix = raw_parts[-1] if len(raw_parts) > 1 else current_fn

        new_fn = (
            pattern.replace("{category}", str(category))
            .replace("{tags}", str(tags_str))
            .replace("{summary}", str(summary_safe))
            .replace("{emotion}", "")
            .replace("{composition}", "")
            .replace("{original_name}", str(original_suffix))
        )

        if find_regex:
            try:
                new_fn = re.sub(find_regex, replace_str, new_fn)
            except re.error:
                pass

        return FileManager.sanitize_filename(new_fn, max_len=200)

    def update_preview(self):
        pattern = self.pattern_input.text()
        find_regex = self.regex_find.text()
        replace_str = self.regex_replace.text()

        if find_regex:
            try:
                re.compile(find_regex)
                self.status_label.setText("正则表达式有效")
                self.status_label.setStyleSheet(
                    f"color: {self._changed_name_color().name()}; background: transparent;"
                )
            except re.error as e:
                self.status_label.setText(f"正则错误: {str(e)}")
                self.status_label.setStyleSheet(
                    f"color: {self._colors['danger']}; background: transparent;"
                )
        else:
            self.status_label.setText("")

        self.preview_table.setRowCount(len(self.selected_videos))
        text_brush = QBrush(QColor(self._colors["text"]))
        changed_brush = QBrush(self._changed_name_color())
        unchanged_brush = QBrush(self._unchanged_name_color())

        for i, item in enumerate(self.selected_videos):
            old_fn = item.get("filename", "")
            new_fn = self.get_preview_filename(item, pattern, find_regex, replace_str)

            old_item = QTableWidgetItem(old_fn)
            old_item.setForeground(text_brush)
            arrow_item = QTableWidgetItem("→")
            arrow_item.setTextAlignment(Qt.AlignCenter)
            arrow_item.setForeground(text_brush)
            new_item = QTableWidgetItem(new_fn)

            if old_fn != new_fn:
                new_item.setForeground(changed_brush)
            else:
                new_item.setForeground(unchanged_brush)

            self.preview_table.setItem(i, 0, old_item)
            self.preview_table.setItem(i, 1, arrow_item)
            self.preview_table.setItem(i, 2, new_item)

    def get_final_config(self):
        return {
            "pattern": self.pattern_input.text(),
            "find_regex": self.regex_find.text(),
            "replace_str": self.regex_replace.text(),
        }