# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QLineEdit, QCompleter
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from .flow_layout import FlowLayout

class TagChip(QFrame):
    """单个标签气泡"""
    clicked = Signal(str)
    deleted = Signal(str)

    def __init__(self, text, color=None, weight=None, is_misspelled=False, icon=None, parent=None):
        super().__init__(parent)
        self.text = text
        self.color = color
        # 标签权重已下线：统一视觉权重
        self.weight = 1.0
        self.is_misspelled = is_misspelled
        self.icon = icon
        self.setObjectName("TagChip")
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 5, 2)
        layout.setSpacing(5)

        # 增加图标支持
        if self.icon:
            icon_label = QLabel()
            if isinstance(self.icon, str) and (self.icon.endswith((".png", ".jpg", ".svg"))):
                pixmap = QPixmap(self.icon)
                if not pixmap.isNull():
                    icon_label.setPixmap(pixmap.scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                # 假设是内置图标名称或 Emoji
                icon_label.setText(self.icon)
            layout.addWidget(icon_label)

        # 固定字号与边框（不再按权重变化）
        font_size = 13
        border_width = 1

        label = QLabel(self.text)
        label.setStyleSheet(f"border: none; background: transparent; font-size: {font_size}px; font-weight: normal;")
        
        # 如果拼写错误，文字变为红色或加下划线
        if self.is_misspelled:
            label.setStyleSheet(label.styleSheet() + "color: #ff4d4f; text-decoration: underline;")
            self.setToolTip("疑似错别字")

        del_btn = QPushButton("×")
        del_btn.setFixedSize(16, 16)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                color: #888;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                color: #ff4d4f;
            }
        """)
        del_btn.clicked.connect(lambda: self.deleted.emit(self.text))

        layout.addWidget(label)
        layout.addWidget(del_btn)
        
        self.setFixedHeight(28)
        
        bg_color = self.color if self.color else "#2b2b2b"
        border_color = self.color if self.color else "#444"
            
        self.setStyleSheet(f"""
            #TagChip {{
                background-color: {bg_color};
                border: {border_width}px solid {border_color};
                border-radius: {self.height() // 2}px;
            }}
            #TagChip:hover {{
                border-color: #3d5afe;
                background-color: #323232;
            }}
        """)

class TagFlowWidget(QWidget):
    """流式标签容器"""
    tags_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tags = []
        self.tag_colors = {} # 标签名 -> 颜色
        self.setup_ui()

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(5)

        # 标签展示区域
        self.tags_container = QWidget()
        self.flow_layout = FlowLayout(self.tags_container, margin=0, spacing=5)
        self.main_layout.addWidget(self.tags_container)

        # 输入区域
        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入标签并按回车...")
        self.input_field.returnPressed.connect(self.add_tag_from_input)
        
        input_layout.addWidget(self.input_field)
        self.main_layout.addLayout(input_layout)

    def set_tags(self, tags, colors=None, weights=None, misspelled_tags=None, icons=None):
        """设置显示的标签列表（weights 参数保留兼容，已忽略）。"""
        # 清空现有标签
        while self.flow_layout.count() > 0:
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.tags = []
        self.tag_colors = colors or {}
        self.tag_weights = {}
        self.tag_icons = icons or {}
        self.misspelled_tags = misspelled_tags or []
        
        for tag in tags:
            if tag and tag not in self.tags:
                color = self.tag_colors.get(tag)
                is_misspelled = tag in self.misspelled_tags
                icon = self.tag_icons.get(tag)
                self.add_tag_chip(tag, color, None, is_misspelled, icon)
        
    def add_tag_from_input(self):
        text = self.input_field.text().strip().replace("，", ",")
        if not text:
            return
        
        # 支持逗号分隔
        new_tags = [t.strip() for t in text.split(",") if t.strip()]
        for tag in new_tags:
            if tag not in self.tags:
                color = self.tag_colors.get(tag)
                icon = self.tag_icons.get(tag)
                is_misspelled = False
                if hasattr(self, "misspelled_tags"):
                     is_misspelled = tag in self.misspelled_tags
                
                self.add_tag_chip(tag, color, None, is_misspelled, icon)
        
        self.input_field.clear()
        self.tags_changed.emit(self.tags)

    def add_tag_chip(self, text, color=None, weight=None, is_misspelled=False, icon=None):
        chip = TagChip(text, color, weight, is_misspelled, icon)
        chip.deleted.connect(self.remove_tag)
        self.flow_layout.addWidget(chip)
        self.tags.append(text)

    def remove_tag(self, text):
        if text in self.tags:
            self.tags.remove(text)
            # 重新渲染所有标签以保持布局正确
            self.set_tags(list(self.tags), self.tag_colors)
            self.tags_changed.emit(self.tags)

    def set_completer(self, words):
        """设置自动补全"""
        completer = QCompleter(words, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.input_field.setCompleter(completer)
