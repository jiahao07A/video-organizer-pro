# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QDateEdit, QCheckBox, QPushButton, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QDate
from .checkable_combo_box import CheckableComboBox
from gui.styles import normalize_theme, get_theme_colors


class FilterPanel(QFrame):
    """高级筛选面板 — 跟随全局 light/dark 主题；工作台与素材库共用。"""
    filterChanged = Signal(dict)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("FilterPanel")
        self.setup_ui()
        self.apply_theme()

    def setup_ui(self):
        self.setFrameShape(QFrame.StyledPanel)
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.cat_combo = CheckableComboBox()
        self.cat_combo.setPlaceholderText("筛选分类...")
        for cat in self.settings.get("categories", []):
            self.cat_combo.add_item(cat)
        self.cat_combo.itemsChanged.connect(self.emit_filter)

        self.tag_combo = CheckableComboBox()
        self.tag_combo.setPlaceholderText("筛选标签...")
        all_tags = set()
        for tags in self.settings.get("tag_dimensions", {}).values():
            all_tags.update(tags)
        for tag in sorted(list(all_tags)):
            self.tag_combo.add_item(tag)
        self.tag_combo.itemsChanged.connect(self.emit_filter)

        self.emotion_combo = CheckableComboBox()
        self.emotion_combo.setPlaceholderText("筛选情绪...")
        for emo in ["平静", "悲伤", "喜悦", "治愈", "震撼", "唯美", "紧张", "庄重", "科技感", "艺术感", "宁静"]:
            self.emotion_combo.add_item(emo)
        self.emotion_combo.itemsChanged.connect(self.emit_filter)

        self.status_combo = CheckableComboBox()
        self.status_combo.setPlaceholderText("分析状态...")
        for s in ["未分析", "已分析", "未命名", "已重命名"]:
            self.status_combo.add_item(s)
        self.status_combo.itemsChanged.connect(self.emit_filter)

        self.tag_mode_combo = QComboBox()
        self.tag_mode_combo.addItem("标签：满足任一", "any")
        self.tag_mode_combo.addItem("标签：同时全部", "all")
        self.tag_mode_combo.currentIndexChanged.connect(self.emit_filter)

        self.summary_combo = QComboBox()
        self.summary_combo.addItem("摘要：不限", "any")
        self.summary_combo.addItem("摘要：为空", "empty")
        self.summary_combo.addItem("摘要：非空", "nonempty")
        self.summary_combo.currentIndexChanged.connect(self.emit_filter)

        self.date_start = QDateEdit()
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addMonths(-1))
        self.date_start.setEnabled(True)
        self.date_start.dateChanged.connect(self._on_date_value_changed)

        self.date_end = QDateEdit()
        self.date_end.setDisplayFormat("yyyy-MM-dd")
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate())
        self.date_end.setEnabled(True)
        self.date_end.dateChanged.connect(self._on_date_value_changed)

        self.dup_cb = QCheckBox("仅显示重复项")
        self.dup_cb.stateChanged.connect(self.emit_filter)

        # 用 toggled(bool)，避免 stateChanged 的 int/CheckState 比较失败导致永远不启用
        self.date_check = QCheckBox("启用日期筛选")
        self.date_check.setToolTip("勾选后按下方起止日期过滤；也可直接改日期（会自动勾选）")
        self.date_check.toggled.connect(self._on_date_filter_toggled)

        layout.addWidget(QLabel("分类:"), 0, 0)
        layout.addWidget(self.cat_combo, 0, 1)
        layout.addWidget(QLabel("标签:"), 0, 2)
        layout.addWidget(self.tag_combo, 0, 3)
        layout.addWidget(self.dup_cb, 0, 4)

        layout.addWidget(QLabel("情绪:"), 1, 0)
        layout.addWidget(self.emotion_combo, 1, 1)
        layout.addWidget(QLabel("状态:"), 1, 2)
        layout.addWidget(self.status_combo, 1, 3)
        layout.addWidget(self.tag_mode_combo, 1, 4)

        layout.addWidget(self.date_check, 2, 0)
        layout.addWidget(self.date_start, 2, 1)
        layout.addWidget(QLabel("至"), 2, 2, alignment=Qt.AlignCenter)
        layout.addWidget(self.date_end, 2, 3)
        layout.addWidget(self.summary_combo, 2, 4)

        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self.reset_filter)
        layout.addWidget(reset_btn, 3, 4)

    def apply_theme(self, theme=None):
        if theme is None:
            theme = self.settings.get("ui_preferences", {}).get("theme", "dark")
        theme = normalize_theme(theme)
        c = get_theme_colors(theme)
        self.setStyleSheet(f"""
            #FilterPanel {{
                background-color: {c["filter_bg"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
                color: {c["text"]};
            }}
            #FilterPanel QLabel {{
                color: {c["text"]};
                background: transparent;
            }}
            #FilterPanel QCheckBox {{
                color: {c["text"]};
                background: transparent;
            }}
            #FilterPanel QLineEdit, #FilterPanel QComboBox, #FilterPanel QDateEdit {{
                background-color: {c["item_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
            }}
            #FilterPanel QDateEdit:disabled {{
                color: {c["text"]};
                background-color: {c["item_bg"]};
            }}
            #FilterPanel QPushButton {{
                background-color: {c["item_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
            }}
        """)

    def _on_date_filter_toggled(self, checked: bool):
        """启用/关闭日期条件；日期控件始终可点，避免「点不动」。"""
        self.emit_filter()

    def _on_date_value_changed(self, *_args):
        """用户改日期时自动打开「启用日期筛选」。"""
        if not self.date_check.isChecked():
            self.date_check.blockSignals(True)
            self.date_check.setChecked(True)
            self.date_check.blockSignals(False)
        self.emit_filter()

    def reset_filter(self):
        self.cat_combo.clear_items()
        for cat in self.settings.get("categories", []):
            self.cat_combo.add_item(cat)
        self.tag_combo.clear_items()
        all_tags = set()
        for tags in self.settings.get("tag_dimensions", {}).values():
            all_tags.update(tags)
        for tag in sorted(list(all_tags)):
            self.tag_combo.add_item(tag)
        self.emotion_combo.clear_items()
        for emo in ["平静", "悲伤", "喜悦", "治愈", "震撼", "唯美", "紧张", "庄重", "科技感", "艺术感", "宁静"]:
            self.emotion_combo.add_item(emo)
        self.status_combo.clear_items()
        for s in ["未分析", "已分析", "未命名", "已重命名"]:
            self.status_combo.add_item(s)
        self.tag_mode_combo.setCurrentIndex(0)
        self.summary_combo.setCurrentIndex(0)
        self.dup_cb.setChecked(False)
        self.date_check.blockSignals(True)
        self.date_check.setChecked(False)
        self.date_check.blockSignals(False)
        self.date_start.blockSignals(True)
        self.date_end.blockSignals(True)
        self.date_start.setDate(QDate.currentDate().addMonths(-1))
        self.date_end.setDate(QDate.currentDate())
        self.date_start.blockSignals(False)
        self.date_end.blockSignals(False)
        self.emit_filter()

    def emit_filter(self):
        params = {
            "categories": self.cat_combo.get_checked_items(),
            "tags": self.tag_combo.get_checked_items(),
            "emotions": self.emotion_combo.get_checked_items(),
            "analysis_statuses": self.status_combo.get_checked_items(),
            "tag_match_mode": self.tag_mode_combo.currentData() or "any",
            "summary_empty": self.summary_combo.currentData() or "any",
            "only_dup": self.dup_cb.isChecked(),
            "date_start": self.date_start.date() if self.date_check.isChecked() else None,
            "date_end": self.date_end.date() if self.date_check.isChecked() else None,
        }
        self.filterChanged.emit(params)