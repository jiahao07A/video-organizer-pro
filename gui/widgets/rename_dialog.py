# -*- coding: utf-8 -*-
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, 
    QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame
)
from PySide6.QtCore import Qt, Signal
from core.video_organizer_service import VideoOrganizerService, FileManager

class BatchRenameDialog(QDialog):
    """批量重命名预览对话框，支持正则表达式预览"""
    def __init__(self, service: VideoOrganizerService, selected_videos, parent=None):
        super().__init__(parent)
        self.service = service
        self.selected_videos = selected_videos
        self.setWindowTitle("高级批量重命名 (正则预览)")
        self.resize(1000, 700)
        self.setup_ui()
        self.update_preview()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # 模式配置区
        config_frame = QFrame()
        config_frame.setStyleSheet("background-color: #262626; border-radius: 8px;")
        config_layout = QVBoxLayout(config_frame)
        
        # 1. 常规模式输入
        pattern_layout = QHBoxLayout()
        pattern_layout.addWidget(QLabel("重命名模式:"))
        self.pattern_input = QLineEdit()
        # 从配置中获取默认模式
        global_settings = self.service.tag_config.get("global_settings", {})
        default_pattern = global_settings.get("export_schemes", {}).get("filename_pattern", "{category}-{tags}-{summary}-{original_name}")
        self.pattern_input.setText(default_pattern)
        self.pattern_input.textChanged.connect(self.update_preview)
        pattern_layout.addWidget(self.pattern_input)
        config_layout.addLayout(pattern_layout)
        
        # 2. 正则查找替换
        regex_layout = QHBoxLayout()
        regex_layout.addWidget(QLabel("正则查找:"))
        self.regex_find = QLineEdit()
        self.regex_find.setPlaceholderText("例如: ^IMG_")
        self.regex_find.textChanged.connect(self.update_preview)
        
        regex_layout.addWidget(self.regex_find)
        regex_layout.addWidget(QLabel("替换为:"))
        self.regex_replace = QLineEdit()
        self.regex_replace.setPlaceholderText("例如: CLIP_")
        self.regex_replace.textChanged.connect(self.update_preview)
        regex_layout.addWidget(self.regex_replace)
        config_layout.addLayout(regex_layout)

        help_label = QLabel("变量支持: {category}, {tags}, {summary}, {original_name}")
        help_label.setStyleSheet("color: #888; font-size: 11px;")
        config_layout.addWidget(help_label)

        layout.addWidget(config_frame)

        # 预览列表
        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(3)
        self.preview_table.setHorizontalHeaderLabels(["当前文件名", "➜", "新文件名预览"])
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.preview_table.setColumnWidth(1, 40)
        self.preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.preview_table)

        # 底部按钮
        btn_layout = QHBoxLayout()
        self.status_label = QLabel("")
        btn_layout.addWidget(self.status_label)
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        
        self.apply_btn = QPushButton("执行重命名")
        self.apply_btn.setObjectName("primary_button")
        self.apply_btn.setStyleSheet("background-color: #3d5afe; color: white; padding: 8px 20px;")
        self.apply_btn.clicked.connect(self.accept)
        
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.apply_btn)
        layout.addLayout(btn_layout)

    def get_preview_filename(self, item, pattern, find_regex, replace_str):
        # 1. 基础变量替换
        category = item.get("category", "Other")
        tags = item.get("tags", [])
        tags_str = "_".join(tags)
        summary_safe = FileManager.sanitize_filename(item.get("summary", ""), max_len=50)
        
        # 处理原始文件名后缀
        current_fn = item.get("filename", "")
        raw_parts = current_fn.split("-")
        original_suffix = raw_parts[-1] if len(raw_parts) > 1 else current_fn
        
        # {emotion}/{composition} 已下线：替换为空
        new_fn = pattern.replace("{category}", str(category))\
                        .replace("{tags}", str(tags_str))\
                        .replace("{summary}", str(summary_safe))\
                        .replace("{emotion}", "")\
                        .replace("{composition}", "")\
                        .replace("{original_name}", str(original_suffix))
        
        # 2. 正则处理
        if find_regex:
            try:
                new_fn = re.sub(find_regex, replace_str, new_fn)
            except re.error:
                # 正则表达式语法错误，不进行替换并提示
                pass
                
        return FileManager.sanitize_filename(new_fn, max_len=200)

    def update_preview(self):
        pattern = self.pattern_input.text()
        find_regex = self.regex_find.text()
        replace_str = self.regex_replace.text()
        
        # 校验正则合法性
        if find_regex:
            try:
                re.compile(find_regex)
                self.status_label.setText("✅ 正则表达式有效")
                self.status_label.setStyleSheet("color: #52C41A;")
            except re.error as e:
                self.status_label.setText(f"❌ 正则错误: {str(e)}")
                self.status_label.setStyleSheet("color: #FF4D4F;")
        else:
            self.status_label.setText("")

        self.preview_table.setRowCount(len(self.selected_videos))
        
        for i, item in enumerate(self.selected_videos):
            old_fn = item.get("filename", "")
            new_fn = self.get_preview_filename(item, pattern, find_regex, replace_str)
            
            old_item = QTableWidgetItem(old_fn)
            arrow_item = QTableWidgetItem("➜")
            arrow_item.setTextAlignment(Qt.AlignCenter)
            new_item = QTableWidgetItem(new_fn)
            
            if old_fn != new_fn:
                new_item.setForeground(Qt.green)
            else:
                new_item.setForeground(Qt.gray)
                
            self.preview_table.setItem(i, 0, old_item)
            self.preview_table.setItem(i, 1, arrow_item)
            self.preview_table.setItem(i, 2, new_item)

    def get_final_config(self):
        return {
            "pattern": self.pattern_input.text(),
            "find_regex": self.regex_find.text(),
            "replace_str": self.regex_replace.text()
        }
