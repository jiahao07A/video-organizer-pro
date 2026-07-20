# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, 
    QFrame, QFormLayout, QGroupBox, QLineEdit, QSpinBox, 
    QCheckBox, QPushButton, QMessageBox, QTabWidget, QPlainTextEdit,
    QComboBox
)
from PySide6.QtCore import Signal, Qt
from core.video_organizer_service import VideoOrganizerService, SettingsManager

class SettingsView(QWidget):
    """设置视图 - 全面重构的高级设置系统"""
    settings_applied = Signal()

    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = service.settings
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        
        title = QLabel("系统设置")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #ce9178;")
        layout.addWidget(title)
        
        self.tabs = QTabWidget()
        self.tabs.setObjectName("settings_tabs")
        
        # 1. 常规设置
        self.tabs.addTab(self.create_general_tab(), "常规")
        
        # 2. AI 引擎
        self.tabs.addTab(self.create_ai_tab(), "AI 引擎")
        
        # 3. Prompt 管理
        self.tabs.addTab(self.create_prompts_tab(), "Prompt 模板")
        
        # 4. 界面设置
        self.tabs.addTab(self.create_interface_tab(), "界面")
        
        # 5. 导出模板 (V6.0 MEGA UPDATE)
        self.tabs.addTab(self.create_export_tab(), "导出模板")
        
        layout.addWidget(self.tabs)
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        
        self.save_btn = QPushButton("保存所有设置")
        self.save_btn.setObjectName("primary_button")
        self.save_btn.setFixedHeight(40)
        self.save_btn.setFixedWidth(150)
        self.save_btn.clicked.connect(self.apply_settings)
        
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.setFixedHeight(40)
        self.reset_btn.setFixedWidth(100)
        self.reset_btn.clicked.connect(self.reset_to_defaults)
        
        btn_layout.addWidget(self.reset_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

    def create_general_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        form = QFormLayout(container)
        form.setSpacing(15)
        
        # 处理配置
        proc_group = QGroupBox("处理配置")
        proc_form = QFormLayout(proc_group)
        
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(SettingsManager.get_setting(self.settings, "processing.max_workers", 4))
        
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 50)
        self.frames_spin.setValue(SettingsManager.get_setting(self.settings, "processing.max_frames", 10))
        
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(10, 100)
        self.quality_spin.setValue(SettingsManager.get_setting(self.settings, "processing.jpeg_quality", 80))
        
        self.scene_detect_cb = QCheckBox("启用智能场景检测")
        self.scene_detect_cb.setChecked(SettingsManager.get_setting(self.settings, "processing.enable_scene_detection", True))
        
        self.audio_cb = QCheckBox("启用音频转录 (Whisper)")
        self.audio_cb.setChecked(SettingsManager.get_setting(self.settings, "processing.enable_audio_transcription", False))
        
        proc_form.addRow("并发处理线程:", self.workers_spin)
        proc_form.addRow("AI 分析抽帧数:", self.frames_spin)
        proc_form.addRow("JPEG 压缩质量:", self.quality_spin)
        proc_form.addRow(self.scene_detect_cb)
        proc_form.addRow(self.audio_cb)
        form.addRow(proc_group)
        
        # 重命名规则
        rename_group = QGroupBox("自动重命名规则")
        rename_form = QFormLayout(rename_group)
        
        self.pattern_input = QLineEdit(SettingsManager.get_setting(self.settings, "rename_pattern", ""))
        self.pattern_input.setPlaceholderText("{category}-{tags}-{summary}-{original_name}")
        
        rename_form.addRow("命名模式:", self.pattern_input)
        rename_form.addRow(QLabel("可用变量: {category}, {tags}, {summary}, {original_name}"))
        form.addRow(rename_group)
        
        scroll.setWidget(container)
        layout.addWidget(scroll)
        return tab

    def create_ai_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        form_group = QGroupBox("API 通用配置")
        form = QFormLayout(form_group)
        
        self.api_key_input = QLineEdit(SettingsManager.get_setting(self.settings, "api.key", ""))
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_url_input = QLineEdit(SettingsManager.get_setting(self.settings, "api.base_url", ""))
        
        form.addRow("API Key:", self.api_key_input)
        form.addRow("API Base URL:", self.api_url_input)
        layout.addWidget(form_group)
        
        model_group = QGroupBox("任务模型路由")
        model_form = QFormLayout(model_group)
        
        self.cls_model_input = QLineEdit(SettingsManager.get_setting(self.settings, "api.model_personalization.video_classification", ""))
        self.tag_model_input = QLineEdit(SettingsManager.get_setting(self.settings, "api.model_personalization.tag_generation", ""))
        self.desc_model_input = QLineEdit(SettingsManager.get_setting(self.settings, "api.model_personalization.content_description", ""))
        
        model_form.addRow("视频分类模型:", self.cls_model_input)
        model_form.addRow("标签生成模型:", self.tag_model_input)
        model_form.addRow("内容描述模型:", self.desc_model_input)
        
        layout.addWidget(model_group)
        layout.addStretch()
        return tab

    def create_prompts_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        vbox = QVBoxLayout(container)
        
        # 分类 Prompt
        vbox.addWidget(QLabel("<b>视频分类任务 Prompt:</b>"))
        self.cls_prompt_edit = QPlainTextEdit()
        self.cls_prompt_edit.setPlainText(SettingsManager.get_setting(self.settings, "prompts.video_classification", ""))
        self.cls_prompt_edit.setMinimumHeight(120)
        vbox.addWidget(self.cls_prompt_edit)
        
        # 标签 Prompt
        vbox.addWidget(QLabel("<b>标签生成任务 Prompt:</b>"))
        self.tag_prompt_edit = QPlainTextEdit()
        self.tag_prompt_edit.setPlainText(SettingsManager.get_setting(self.settings, "prompts.tag_generation", ""))
        self.tag_prompt_edit.setMinimumHeight(120)
        vbox.addWidget(self.tag_prompt_edit)
        
        # 描述 Prompt
        vbox.addWidget(QLabel("<b>内容描述任务 Prompt:</b>"))
        self.desc_prompt_edit = QPlainTextEdit()
        self.desc_prompt_edit.setPlainText(SettingsManager.get_setting(self.settings, "prompts.content_description", ""))
        self.desc_prompt_edit.setMinimumHeight(120)
        vbox.addWidget(self.desc_prompt_edit)
        
        scroll.setWidget(container)
        layout.addWidget(scroll)
        return tab

    def create_interface_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        layout = QFormLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        self.font_spin = QSpinBox()
        self.font_spin.setRange(10, 30)
        self.font_spin.setValue(SettingsManager.get_setting(self.settings, "ui_preferences.font_size", 14))
        
        self.thumb_w_spin = QSpinBox()
        self.thumb_w_spin.setRange(80, 400)
        self.thumb_w_spin.setValue(SettingsManager.get_setting(self.settings, "ui_preferences.thumbnail_size", [160, 90])[0])
        
        self.thumb_h_spin = QSpinBox()
        self.thumb_h_spin.setRange(45, 300)
        self.thumb_h_spin.setValue(SettingsManager.get_setting(self.settings, "ui_preferences.thumbnail_size", [160, 90])[1])
        
        layout.addRow("全局字体大小:", self.font_spin)
        
        thumb_layout = QHBoxLayout()
        thumb_layout.addWidget(self.thumb_w_spin)
        thumb_layout.addWidget(QLabel("x"))
        thumb_layout.addWidget(self.thumb_h_spin)
        layout.addRow("缩略图尺寸 (WxH):", thumb_layout)
        
        scroll.setWidget(container)
        main_layout.addWidget(scroll)
        return tab

    def create_export_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        form = QFormLayout(container)
        form.setSpacing(15)
        
        # 1. 重命名模板
        rename_group = QGroupBox("文件名模板")
        rename_form = QFormLayout(rename_group)
        
        self.filename_tmpl = QLineEdit()
        pattern = SettingsManager.get_setting(self.settings, "rename_pattern", "{category}-{tags}-{summary}-{original_name}")
        self.filename_tmpl.setText(pattern)
        
        vars_hint = QLabel("变量: {date}, {category}, {tags}, {summary}, {emotion}, {composition}, {original_name}")
        vars_hint.setStyleSheet("color: #888; font-size: 11px;")
        
        rename_form.addRow("模式:", self.filename_tmpl)
        rename_form.addRow("", vars_hint)
        form.addRow(rename_group)
        
        # 2. XMP 导出设置
        xmp_group = QGroupBox("XMP 元数据配置")
        xmp_form = QFormLayout(xmp_group)
        
        self.xmp_lang_combo = QComboBox()
        self.xmp_lang_combo.addItems(["zh-CN", "en"])
        cur_lang = SettingsManager.get_setting(self.settings, "global_settings.language", "zh-CN")
        self.xmp_lang_combo.setCurrentText(cur_lang)
        
        self.xmp_hierarchical_cb = QCheckBox("启用层级标签 (Parent|Child)")
        self.xmp_hierarchical_cb.setChecked(SettingsManager.get_setting(self.settings, "global_settings.export_schemes.xmp_hierarchical", True))
        
        self.xmp_prefix_cat_cb = QCheckBox("在标签前增加分类前缀")
        self.xmp_prefix_cat_cb.setChecked(SettingsManager.get_setting(self.settings, "global_settings.export_schemes.xmp_prefix_category", True))
        
        xmp_form.addRow("导出语言:", self.xmp_lang_combo)
        xmp_form.addRow(self.xmp_hierarchical_cb)
        xmp_form.addRow(self.xmp_prefix_cat_cb)
        form.addRow(xmp_group)
        
        # 3. ALE 导出列定义 (预览)
        ale_group = QGroupBox("ALE 导出列定义 (CSV 格式)")
        ale_layout = QVBoxLayout(ale_group)
        self.ale_columns_edit = QLineEdit(", ".join(SettingsManager.get_setting(self.settings, "global_settings.export_schemes.ale_columns", ["Name", "Keywords", "Category", "Summary", "Emotion"])))
        ale_layout.addWidget(QLabel("字段列表 (逗号分隔):"))
        ale_layout.addWidget(self.ale_columns_edit)
        form.addRow(ale_group)
        
        scroll.setWidget(container)
        layout.addWidget(scroll)
        return tab

    def apply_settings(self):
        """同步 UI 数据到 settings 字典并保存"""
        # API
        SettingsManager.update_setting(self.settings, "api.key", self.api_key_input.text().strip())
        SettingsManager.update_setting(self.settings, "api.base_url", self.api_url_input.text().strip())
        SettingsManager.update_setting(self.settings, "api.model_personalization.video_classification", self.cls_model_input.text().strip())
        SettingsManager.update_setting(self.settings, "api.model_personalization.tag_generation", self.tag_model_input.text().strip())
        SettingsManager.update_setting(self.settings, "api.model_personalization.content_description", self.desc_model_input.text().strip())
        
        # Processing
        SettingsManager.update_setting(self.settings, "processing.max_workers", self.workers_spin.value())
        SettingsManager.update_setting(self.settings, "processing.max_frames", self.frames_spin.value())
        SettingsManager.update_setting(self.settings, "processing.jpeg_quality", self.quality_spin.value())
        SettingsManager.update_setting(self.settings, "processing.enable_scene_detection", self.scene_detect_cb.isChecked())
        SettingsManager.update_setting(self.settings, "processing.enable_audio_transcription", self.audio_cb.isChecked())
        
        # Prompts
        SettingsManager.update_setting(self.settings, "prompts.video_classification", self.cls_prompt_edit.toPlainText().strip())
        SettingsManager.update_setting(self.settings, "prompts.tag_generation", self.tag_prompt_edit.toPlainText().strip())
        SettingsManager.update_setting(self.settings, "prompts.content_description", self.desc_prompt_edit.toPlainText().strip())
        
        # UI & Others
        SettingsManager.update_setting(self.settings, "ui_preferences.font_size", self.font_spin.value())
        SettingsManager.update_setting(self.settings, "ui_preferences.thumbnail_size", [self.thumb_w_spin.value(), self.thumb_h_spin.value()])
        
        # V6.0 Export Templates
        SettingsManager.update_setting(self.settings, "rename_pattern", self.filename_tmpl.text().strip())
        SettingsManager.update_setting(self.settings, "global_settings.language", self.xmp_lang_combo.currentText())
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.xmp_hierarchical", self.xmp_hierarchical_cb.isChecked())
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.xmp_prefix_category", self.xmp_prefix_cat_cb.isChecked())
        
        ale_cols = [c.strip() for c in self.ale_columns_edit.text().split(",") if c.strip()]
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.ale_columns", ale_cols)
        
        try:
            SettingsManager.save_settings(self.settings, self.service.db)
            QMessageBox.information(self, "成功", "设置已保存。部分视觉更新可能需要重启应用生效。")
            self.settings_applied.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存设置失败: {e}")

    def reset_to_defaults(self):
        reply = QMessageBox.question(self, "确认", "确定要恢复所有设置为默认值吗？这将覆盖当前配置。",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            from core.video_organizer_service import DEFAULT_SETTINGS
            self.settings = DEFAULT_SETTINGS.copy()
            # 重新加载 UI 即可
            self.setup_ui()
            QMessageBox.information(self, "已重置", "设置已重置为默认值，请点击保存以生效。")
