# -*- coding: utf-8 -*-
import os
import re
from datetime import datetime
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
        self._preview_date = datetime.now().strftime("%Y%m%d")
        self._preview_rows = []
        self._excluded_paths = set()
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

        self.help_label = QLabel(
            "变量支持: {category}, {tags}, {summary}, {original_name}, {date}。"
            "执行路径预览按完整路径显示；已存在的请求目标会标记冲突。"
        )
        config_layout.addWidget(self.help_label)

        layout.addWidget(self.config_frame)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("搜索源路径、请求目标路径或风险状态…")
        self.search_input.textChanged.connect(self._filter_rows)
        layout.addWidget(self.search_input)

        self.preview_summary_label = QLabel()
        self.preview_summary_label.setWordWrap(True)
        layout.addWidget(self.preview_summary_label)

        self.preview_table = QTableWidget()
        self.preview_table.setObjectName("rename_preview_table")
        self.preview_table.setColumnCount(4)
        self.preview_table.setHorizontalHeaderLabels(
            ["执行", "源路径", "请求目标路径", "状态"]
        )
        self.preview_table.setSortingEnabled(True)
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.preview_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.preview_table.setColumnWidth(1, 420)
        self.preview_table.setColumnWidth(2, 420)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.itemChanged.connect(self._on_preview_item_changed)
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

        current_fn = os.path.basename(str(item.get("path") or "")) or item.get("filename", "")
        raw_parts = current_fn.split("-")
        original_suffix = raw_parts[-1] if len(raw_parts) > 1 else current_fn

        new_fn = (
            pattern.replace("{category}", str(category))
            .replace("{tags}", str(tags_str))
            .replace("{summary}", str(summary_safe))
            .replace("{emotion}", "")
            .replace("{composition}", "")
            .replace("{date}", self._preview_date)
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

        regex_valid = True
        if find_regex:
            try:
                re.compile(find_regex)
                self.status_label.setText("正则表达式有效")
                self.status_label.setStyleSheet(
                    f"color: {self._changed_name_color().name()}; background: transparent;"
                )
            except re.error as e:
                regex_valid = False
                self.status_label.setText(f"正则错误: {str(e)}")
                self.status_label.setStyleSheet(
                    f"color: {self._colors['danger']}; background: transparent;"
                )
        else:
            self.status_label.setText("")

        self._preview_rows = []
        for video in self.selected_videos:
            source_path = str(video.get("path") or "")
            if not source_path:
                continue
            new_name = self.get_preview_filename(video, pattern, find_regex, replace_str)
            target_path = os.path.join(os.path.dirname(source_path), new_name)
            source_key = self._path_key(source_path)
            target_key = self._path_key(target_path)
            unchanged = source_key == target_key
            target_exists = not unchanged and os.path.exists(target_path)
            self._preview_rows.append({
                "source_path": source_path,
                "target_path": target_path,
                "source_key": source_key,
                "target_key": target_key,
                "unchanged": unchanged,
                "target_exists": target_exists,
            })

        planned_targets = {}
        for row in self._preview_rows:
            if not row["unchanged"]:
                planned_targets.setdefault(row["target_key"], []).append(row)
        for rows in planned_targets.values():
            if len(rows) > 1:
                for row in rows:
                    row["duplicate_planned_target"] = True

        self.preview_table.setSortingEnabled(False)
        self.preview_table.blockSignals(True)
        self.preview_table.setRowCount(len(self._preview_rows))
        text_brush = QBrush(QColor(self._colors["text"]))
        changed_brush = QBrush(self._changed_name_color())
        unchanged_brush = QBrush(self._unchanged_name_color())

        for row_index, row in enumerate(self._preview_rows):
            check_item = QTableWidgetItem()
            check_item.setFlags(check_item.flags() | Qt.ItemIsUserCheckable)
            excluded = row["source_key"] in self._excluded_paths
            check_item.setCheckState(Qt.Unchecked if excluded else Qt.Checked)
            check_item.setToolTip("取消勾选可从本次重命名中排除")
            self.preview_table.setItem(row_index, 0, check_item)

            source_item = QTableWidgetItem(row["source_path"])
            source_item.setForeground(text_brush)
            source_item.setToolTip(row["source_path"])
            target_item = QTableWidgetItem(row["target_path"])
            target_item.setForeground(unchanged_brush if row["unchanged"] else changed_brush)
            target_item.setToolTip(row["target_path"])

            if row["unchanged"]:
                state = "无需更名"
            elif row["target_exists"]:
                state = "目标已存在；执行时可能改用冲突路径"
            elif row.get("duplicate_planned_target"):
                state = "方案内目标重名；执行时可能改用冲突路径"
            else:
                state = "可执行"
            state_item = QTableWidgetItem(state)
            if row["target_exists"] or row.get("duplicate_planned_target"):
                state_item.setForeground(QBrush(QColor(self._colors["danger"])))
            elif row["unchanged"]:
                state_item.setForeground(unchanged_brush)
            else:
                state_item.setForeground(text_brush)
            state_item.setToolTip(state)
            self.preview_table.setItem(row_index, 1, source_item)
            self.preview_table.setItem(row_index, 2, target_item)
            self.preview_table.setItem(row_index, 3, state_item)

        self.preview_table.blockSignals(False)
        self.preview_table.setSortingEnabled(True)
        self._filter_rows(self.search_input.text())
        self._update_preview_summary()
        self.apply_btn.setEnabled(regex_valid and bool(self.selected_source_paths()))

    @staticmethod
    def _path_key(path):
        return os.path.normcase(os.path.normpath(str(path or "")))

    def _on_preview_item_changed(self, item):
        if item.column() != 0:
            return
        source_item = self.preview_table.item(item.row(), 1)
        if source_item is None:
            return
        source_key = self._path_key(source_item.text())
        if item.checkState() == Qt.Checked:
            self._excluded_paths.discard(source_key)
        else:
            self._excluded_paths.add(source_key)
        self._update_preview_summary()

    def _filter_rows(self, text):
        query = str(text or "").strip().casefold()
        for row in range(self.preview_table.rowCount()):
            content = " ".join(
                self.preview_table.item(row, column).text()
                for column in range(1, self.preview_table.columnCount())
                if self.preview_table.item(row, column) is not None
            ).casefold()
            self.preview_table.setRowHidden(row, bool(query and query not in content))

    def _update_preview_summary(self):
        active_rows = [
            row for row in self._preview_rows
            if row["source_key"] not in self._excluded_paths
        ]
        unchanged = sum(1 for row in active_rows if row["unchanged"])
        conflicts = sum(
            1 for row in active_rows
            if row["target_exists"] or row.get("duplicate_planned_target")
        )
        excluded_count = len(self._preview_rows) - len(active_rows)
        self.preview_summary_label.setText(
            f"预览条目 {len(self._preview_rows)} 条；当前选择 {len(active_rows)} 条；"
            f"排除 {excluded_count} 条；无需更名 {unchanged} 条；目标冲突 {conflicts} 条。"
            + (" 冲突时执行路径可能与请求目标路径不同。" if conflicts else "")
        )
        regex_valid = not (
            self.regex_find.text() and self.status_label.text().startswith("正则错误")
        )
        self.apply_btn.setEnabled(regex_valid and bool(self.selected_source_paths()))

    @property
    def active_conflict_count(self):
        return sum(
            1 for row in self._preview_rows
            if row["source_key"] not in self._excluded_paths
            and (row["target_exists"] or row.get("duplicate_planned_target"))
        )

    @property
    def excluded_count(self):
        return sum(
            1 for row in self._preview_rows
            if row["source_key"] in self._excluded_paths
        )

    def selected_source_paths(self):
        """返回按原始方案顺序保留的源路径快照。"""
        return [
            row["source_path"] for row in self._preview_rows
            if row["source_key"] not in self._excluded_paths and not row["unchanged"]
        ]

    def get_final_config(self):
        return {
            "pattern": self.pattern_input.text(),
            "find_regex": self.regex_find.text(),
            "replace_str": self.regex_replace.text(),
            "preview_date": self._preview_date,
        }
