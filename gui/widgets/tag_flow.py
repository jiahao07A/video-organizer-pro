# -*- coding: utf-8 -*-
from typing import Optional

from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QCompleter,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from .flow_layout import FlowLayout
from core.tag_ai_assist import chip_style_tokens, GROUP_ACCENT_COLORS


def _accent_to_group(accent: Optional[str] = None) -> str:
    if not accent:
        return "custom"
    a = str(accent).strip()
    for gid, color in GROUP_ACCENT_COLORS.items():
        if color.upper() == a.upper():
            return gid
    mapping = {
        "#E91E63": "mood",
        "#2196F3": "subject",
        "#4CAF50": "location",
        "#FF9800": "action",
        "#757575": "custom",
    }
    return mapping.get(a.upper(), mapping.get(a, "custom"))


class TagChip(QFrame):
    """单个标签气泡：浅底 + 组色左边条 + 主题正文色"""

    clicked = Signal(str)
    deleted = Signal(str)

    def __init__(
        self,
        text,
        color=None,
        weight=None,
        is_misspelled=False,
        icon=None,
        group_id=None,
        theme="dark",
        parent=None,
    ):
        super().__init__(parent)
        self.text = text
        self.color = color
        self.group_id = group_id or _accent_to_group(color)
        self.theme = theme
        self.weight = 1.0
        self.is_misspelled = is_misspelled
        self.icon = icon
        self.setObjectName("TagChip")
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 5, 2)
        layout.setSpacing(5)

        if self.icon:
            icon_label = QLabel()
            if isinstance(self.icon, str) and self.icon.endswith((".png", ".jpg", ".svg")):
                pixmap = QPixmap(self.icon)
                if not pixmap.isNull():
                    icon_label.setPixmap(
                        pixmap.scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
            else:
                icon_label.setText(self.icon)
            layout.addWidget(icon_label)

        tokens = chip_style_tokens(
            self.group_id,
            self.color,
            theme=self.theme,
            misspelled=self.is_misspelled,
        )

        label = QLabel(self.text)
        label.setStyleSheet(
            "border: none; background: transparent; font-size: 13px; "
            f"font-weight: normal; color: {tokens['text']};"
        )
        if self.is_misspelled:
            self.setToolTip("疑似错别字")

        del_btn = QPushButton("×")
        del_btn.setFixedSize(16, 16)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setStyleSheet(
            f"""
            QPushButton {{
                border: none;
                background: transparent;
                color: {tokens['muted']};
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{
                color: #ff4d4f;
            }}
            """
        )
        del_btn.clicked.connect(lambda: self.deleted.emit(self.text))

        layout.addWidget(label)
        layout.addWidget(del_btn)

        self.setFixedHeight(28)
        self.setStyleSheet(
            f"""
            #TagChip {{
                background-color: {tokens['background']};
                border: 1px solid {tokens['border']};
                border-left: 4px solid {tokens['accent_bar']};
                border-radius: 12px;
            }}
            #TagChip:hover {{
                background-color: {tokens['hover_background']};
                border-color: #3d5afe;
                border-left: 4px solid {tokens['accent_bar']};
            }}
            """
        )


class TagFlowWidget(QWidget):
    """流式标签容器"""

    tags_changed = Signal(list)

    def __init__(self, parent=None, theme="dark"):
        super().__init__(parent)
        self.tags = []
        self.tag_colors = {}
        self.tag_groups = {}
        self.theme = theme
        self.setup_ui()

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(5)

        self.tags_container = QWidget()
        self.flow_layout = FlowLayout(self.tags_container, margin=0, spacing=5)
        self.main_layout.addWidget(self.tags_container)

        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入标签并按回车...")
        self.input_field.returnPressed.connect(self.add_tag_from_input)
        input_layout.addWidget(self.input_field)
        self.main_layout.addLayout(input_layout)

    def set_theme(self, theme: str):
        self.theme = theme or "dark"

    def set_tags(
        self,
        tags,
        colors=None,
        weights=None,
        misspelled_tags=None,
        icons=None,
        groups=None,
    ):
        while self.flow_layout.count() > 0:
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.tags = []
        self.tag_colors = colors or {}
        self.tag_groups = groups or {}
        self.tag_icons = icons or {}
        self.misspelled_tags = misspelled_tags or []

        for tag in tags:
            if tag and tag not in self.tags:
                self.add_tag_chip(
                    tag,
                    self.tag_colors.get(tag),
                    None,
                    tag in self.misspelled_tags,
                    self.tag_icons.get(tag),
                    self.tag_groups.get(tag),
                )

    def add_tag_from_input(self):
        text = self.input_field.text().strip().replace("，", ",")
        if not text:
            return
        for tag in [t.strip() for t in text.split(",") if t.strip()]:
            if tag not in self.tags:
                self.add_tag_chip(
                    tag,
                    self.tag_colors.get(tag),
                    None,
                    tag in getattr(self, "misspelled_tags", []),
                    getattr(self, "tag_icons", {}).get(tag),
                    self.tag_groups.get(tag),
                )
        self.input_field.clear()
        self.tags_changed.emit(self.tags)

    def add_tag_chip(
        self, text, color=None, weight=None, is_misspelled=False, icon=None, group_id=None
    ):
        chip = TagChip(
            text,
            color,
            weight,
            is_misspelled,
            icon,
            group_id=group_id,
            theme=self.theme,
        )
        chip.deleted.connect(self.remove_tag)
        self.flow_layout.addWidget(chip)
        self.tags.append(text)

    def remove_tag(self, text):
        if text in self.tags:
            self.tags.remove(text)
            self.set_tags(
                list(self.tags),
                self.tag_colors,
                misspelled_tags=getattr(self, "misspelled_tags", None),
                icons=getattr(self, "tag_icons", None),
                groups=self.tag_groups,
            )
            self.tags_changed.emit(self.tags)

    def set_completer(self, words):
        completer = QCompleter(words, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.input_field.setCompleter(completer)