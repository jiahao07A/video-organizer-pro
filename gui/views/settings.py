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
        """编辑全局 system 角色头，并预览分析时真正发出的完整提示词。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setSpacing(12)

        hint = QLabel(
            "<b>提示词由两层组成，不是只有下面这一句：</b>"
            "<br>① <b>全局角色头（可编辑）</b>：你在下方写的 system 开头；"
            "<br>② <b>自动组装正文（只读预览）</b>：分类列表、各标签组 local_prompt、"
            "标签库约束、JSON 输出结构——分析时由程序拼进 user 消息，并附上视频帧。"
            "<br>标签组局部说明请到「标签库 → 组规则 / Prompt 配置」维护。"
            "<br><span style='color:#888;'>旧版三份任务 Prompt 已废弃，不再参与分析。</span>"
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.RichText)
        vbox.addWidget(hint)

        vbox.addWidget(QLabel("<b>① 全局角色头（写入 tag_config.global_settings.system_prompt）:</b>"))
        self.system_prompt_edit = QPlainTextEdit()
        default_system = (
            "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。"
        )
        current = ""
        if hasattr(self.service, "get_system_prompt"):
            current = self.service.get_system_prompt()
        else:
            current = (self.service.tag_config or {}).get("global_settings", {}).get("system_prompt", "")
        if not (current or "").strip():
            legacy_parts = [
                SettingsManager.get_setting(self.settings, "prompts.video_classification", ""),
                SettingsManager.get_setting(self.settings, "prompts.tag_generation", ""),
                SettingsManager.get_setting(self.settings, "prompts.content_description", ""),
            ]
            legacy = "\n\n".join(p for p in legacy_parts if p)
            current = legacy or default_system
        self.system_prompt_edit.setPlainText(current)
        self.system_prompt_edit.setMinimumHeight(100)
        self.system_prompt_edit.setPlaceholderText(
            "例如角色、风格偏好、禁止事项。不必重复写 JSON 字段——下方预览会自动带上。"
        )
        vbox.addWidget(self.system_prompt_edit)

        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("<b>② 完整提示词预览（与真实分析同一套组装逻辑）:</b>"))
        preview_header.addStretch()
        refresh_btn = QPushButton("刷新预览")
        refresh_btn.clicked.connect(self.refresh_prompt_preview)
        preview_header.addWidget(refresh_btn)
        vbox.addLayout(preview_header)

        self.prompt_preview_edit = QPlainTextEdit()
        self.prompt_preview_edit.setReadOnly(True)
        self.prompt_preview_edit.setMinimumHeight(280)
        self.prompt_preview_edit.setPlaceholderText("点击「刷新预览」查看将发给模型的 system + user 全文。")
        vbox.addWidget(self.prompt_preview_edit)

        note = QLabel(
            "预览不含视频帧图片。若标签库仍是「测试氛围1」这类占位标签，"
            "预览正文也会偏薄——那是标签库数据问题，不是提示词引擎只有一句话。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 12px;")
        vbox.addWidget(note)
        vbox.addStretch()
        
        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 进入页面即展示一次真实拼装结果
        self.refresh_prompt_preview()
        return tab

    def refresh_prompt_preview(self):
        """按当前编辑框中的角色头 + 现有 tag_config 刷新完整提示词预览。"""
        if not hasattr(self, "prompt_preview_edit"):
            return
        header = ""
        if hasattr(self, "system_prompt_edit"):
            header = self.system_prompt_edit.toPlainText()
        try:
            if hasattr(self.service, "preview_analysis_prompts"):
                prompts = self.service.preview_analysis_prompts(header)
            elif hasattr(self.service, "ai") and hasattr(self.service.ai, "build_analysis_prompts"):
                self.service.ai.tag_config = self.service.tag_config
                prompts = self.service.ai.build_analysis_prompts(header)
            else:
                prompts = {"system_prompt": header, "user_prompt": "（无法生成预览）"}
            text = (
                "======== [system] 发给模型的系统消息 ========\n"
                f"{prompts.get('system_prompt', '')}\n\n"
                "======== [user] 发给模型的用户消息（另附视频帧） ========\n"
                f"{prompts.get('user_prompt', '')}"
            )
            self.prompt_preview_edit.setPlainText(text)
        except Exception as e:
            self.prompt_preview_edit.setPlainText(f"预览失败: {e}")

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

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("浅色", "light")
        cur_theme = SettingsManager.get_setting(self.settings, "ui_preferences.theme", "dark")
        idx = 0 if str(cur_theme).lower() in ("dark", "amber_gold") else 1
        self.theme_combo.setCurrentIndex(idx)

        self.default_view_combo = QComboBox()
        self.default_view_combo.addItem("列表", "list")
        self.default_view_combo.addItem("卡片", "card")
        dv = SettingsManager.get_setting(self.settings, "ui_preferences.default_view", "list")
        self.default_view_combo.setCurrentIndex(0 if dv != "card" else 1)

        self.sidebar_spin = QSpinBox()
        self.sidebar_spin.setRange(160, 360)
        self.sidebar_spin.setValue(int(SettingsManager.get_setting(self.settings, "ui_preferences.sidebar_width", 220) or 220))

        self.detail_expanded_cb = QCheckBox("详情面板默认展开")
        self.detail_expanded_cb.setChecked(
            bool(SettingsManager.get_setting(self.settings, "ui_preferences.detail_panel_expanded", True))
        )

        self.remember_scope_cb = QCheckBox("记住上次工作范围（启动时恢复）")
        self.remember_scope_cb.setChecked(
            bool(SettingsManager.get_setting(self.settings, "ui_preferences.remember_work_scope", False))
        )
        
        layout.addRow("全局字体大小:", self.font_spin)
        
        thumb_layout = QHBoxLayout()
        thumb_layout.addWidget(self.thumb_w_spin)
        thumb_layout.addWidget(QLabel("x"))
        thumb_layout.addWidget(self.thumb_h_spin)
        layout.addRow("缩略图尺寸 (WxH):", thumb_layout)
        layout.addRow("主题:", self.theme_combo)
        layout.addRow("默认视图:", self.default_view_combo)
        layout.addRow("侧边栏宽度:", self.sidebar_spin)
        layout.addRow(self.detail_expanded_cb)
        layout.addRow(self.remember_scope_cb)
        
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
        
        vars_hint = QLabel("变量: {date}, {category}, {tags}, {summary}, {original_name}")
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
        self.ale_columns_edit = QLineEdit(", ".join(SettingsManager.get_setting(self.settings, "global_settings.export_schemes.ale_columns", ["Name", "Keywords", "Category", "Summary"])))
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
        
        # 全局 system_prompt → tag_config（主分析真实入口）；旧 prompts.* 不再写入
        if hasattr(self, "system_prompt_edit"):
            system_prompt = self.system_prompt_edit.toPlainText().strip()
            if hasattr(self.service, "set_system_prompt"):
                self.service.set_system_prompt(system_prompt)
            else:
                self.service.tag_config.setdefault("global_settings", {})["system_prompt"] = system_prompt
                self.service.save_tag_config(self.service.tag_config)
        
        # UI & Others
        SettingsManager.update_setting(self.settings, "ui_preferences.font_size", self.font_spin.value())
        SettingsManager.update_setting(self.settings, "ui_preferences.thumbnail_size", [self.thumb_w_spin.value(), self.thumb_h_spin.value()])
        if hasattr(self, "theme_combo"):
            SettingsManager.update_setting(
                self.settings, "ui_preferences.theme", self.theme_combo.currentData() or "dark"
            )
        if hasattr(self, "default_view_combo"):
            SettingsManager.update_setting(
                self.settings, "ui_preferences.default_view", self.default_view_combo.currentData() or "list"
            )
        if hasattr(self, "sidebar_spin"):
            SettingsManager.update_setting(
                self.settings, "ui_preferences.sidebar_width", self.sidebar_spin.value()
            )
        if hasattr(self, "detail_expanded_cb"):
            SettingsManager.update_setting(
                self.settings, "ui_preferences.detail_panel_expanded", self.detail_expanded_cb.isChecked()
            )
        if hasattr(self, "remember_scope_cb"):
            SettingsManager.update_setting(
                self.settings, "ui_preferences.remember_work_scope", self.remember_scope_cb.isChecked()
            )
            # 立即按开关持久化或清空 last_work_scope 记录策略
            if self.remember_scope_cb.isChecked():
                self.service.persist_work_scope_if_enabled()
        
        # V6.0 Export Templates
        SettingsManager.update_setting(self.settings, "rename_pattern", self.filename_tmpl.text().strip())
        SettingsManager.update_setting(self.settings, "global_settings.language", self.xmp_lang_combo.currentText())
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.xmp_hierarchical", self.xmp_hierarchical_cb.isChecked())
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.xmp_prefix_category", self.xmp_prefix_cat_cb.isChecked())
        
        ale_cols = [c.strip() for c in self.ale_columns_edit.text().split(",") if c.strip()]
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.ale_columns", ale_cols)
        
        try:
            SettingsManager.save_settings(self.settings, self.service.db)
            QMessageBox.information(self, "成功", "设置已保存，界面偏好将尽量立即生效。")
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
