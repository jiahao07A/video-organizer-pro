# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QDateEdit, QCheckBox, QPushButton
from PySide6.QtCore import Qt, Signal, QDate
from .checkable_combo_box import CheckableComboBox
from gui.styles import normalize_theme, get_theme_colors


class FilterPanel(QFrame):
    """高级筛选面板 — 跟随全局 light/dark 主题"""
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
        
        # 分类
        self.cat_combo = CheckableComboBox()
        self.cat_combo.setPlaceholderText("筛选分类...")
        for cat in self.settings.get("categories", []):
            self.cat_combo.add_item(cat)
        self.cat_combo.itemsChanged.connect(self.emit_filter)
        
        # 标签
        self.tag_combo = CheckableComboBox()
        self.tag_combo.setPlaceholderText("筛选标签...")
        all_tags = set()
        for tags in self.settings.get("tag_dimensions", {}).values():
            all_tags.update(tags)
        for tag in sorted(list(all_tags)):
            self.tag_combo.add_item(tag)
        self.tag_combo.itemsChanged.connect(self.emit_filter)
        
        # 日期
        self.date_start = QDateEdit()
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addMonths(-1))
        self.date_start.dateChanged.connect(self.emit_filter)
        
        self.date_end = QDateEdit()
        self.date_end.setDisplayFormat("yyyy-MM-dd")
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate())
        self.date_end.dateChanged.connect(self.emit_filter)
        
        # 开关
        self.dup_cb = QCheckBox("仅显示重复项")
        self.dup_cb.stateChanged.connect(self.emit_filter)
        
        self.date_check = QCheckBox("日期范围:")
        self.date_check.stateChanged.connect(self.toggle_date_filter)
        self.date_start.setEnabled(False)
        self.date_end.setEnabled(False)

        # 布局
        layout.addWidget(QLabel("分类:"), 0, 0)
        layout.addWidget(self.cat_combo, 0, 1)
        layout.addWidget(QLabel("标签:"), 0, 2)
        layout.addWidget(self.tag_combo, 0, 3)
        layout.addWidget(self.dup_cb, 0, 4)
        
        layout.addWidget(self.date_check, 1, 0)
        layout.addWidget(self.date_start, 1, 1)
        layout.addWidget(QLabel("至"), 1, 2, alignment=Qt.AlignCenter)
        layout.addWidget(self.date_end, 1, 3)
        
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self.reset_filter)
        layout.addWidget(reset_btn, 1, 4)

    def apply_theme(self, theme=None):
        """按全局主题重涂筛选面板（去掉写死深色底）。"""
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
            #FilterPanel QPushButton {{
                background-color: {c["item_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
            }}
        """)

    def toggle_date_filter(self, state):
        enabled = state == Qt.Checked
        self.date_start.setEnabled(enabled)
        self.date_end.setEnabled(enabled)
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
            
        self.dup_cb.setChecked(False)
        self.date_check.setChecked(False)
        self.emit_filter()

    def emit_filter(self):
        params = {
            "categories": self.cat_combo.get_checked_items(),
            "tags": self.tag_combo.get_checked_items(),
            "only_dup": self.dup_cb.isChecked(),
            "date_start": self.date_start.date() if self.date_check.isChecked() else None,
            "date_end": self.date_end.date() if self.date_check.isChecked() else None
        }
        self.filterChanged.emit(params)