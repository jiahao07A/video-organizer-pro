# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, 
    QFrame, QFormLayout, QGroupBox, QLineEdit, QSpinBox, 
    QCheckBox, QPushButton, QMessageBox, QTabWidget, QPlainTextEdit,
    QComboBox, QListWidget, QListWidgetItem, QAbstractItemView
)
from PySide6.QtCore import Signal, Qt
from core.video_organizer_service import VideoOrganizerService, SettingsManager
from core.model_providers import (
    MAX_PROVIDERS,
    ProviderLimitError,
    ProviderError,
    ensure_providers,
    list_providers,
    add_provider,
    remove_provider,
    set_current_provider,
    update_provider,
    resolve_current_provider,
    TASK_ROUTE_KEYS,
    TASK_ROUTE_LABELS,
    get_task_model_routing,
    replace_task_model_routing,
    resolve_task_route,
)

from gui.models.settings_catalog import (
    AUTOSAVE,
    list_categories,
    search_entries,
)
from gui.services.settings_controller import SettingsController


class SettingsView(QWidget):
    """设置视图 - 可搜索目录 + 按用户任务分类导航的高级设置系统"""
    settings_applied = Signal()
    ui_preferences_applied = Signal()  # 普通偏好应用不触发 AI 客户端重建。

    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = service.settings
        self._provider_ui_ready = False
        self._suppress_provider_signals = False
        # 普通界面偏好：防抖自动保存并显示状态
        self.prefs_controller = SettingsController(service, parent=self)
        self.prefs_controller.pending.connect(
            lambda: self._set_save_status("界面偏好有改动，正在保存…")
        )
        self.prefs_controller.saved.connect(self._on_preferences_saved)
        self.prefs_controller.save_failed.connect(
            lambda msg: self._set_save_status(f"界面偏好保存失败：{msg}", error=True)
        )
        self._search_widget_map = {}
        self._entry_status_labels = {}
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        
        title = QLabel("系统设置")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #ce9178;")
        layout.addWidget(title)

        # 可搜索设置目录：按用户任务分类导航
        layout.addWidget(self._build_search_bar())
        
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

        # 所有页签建立后，为普通界面偏好绑定防抖自动保存
        self.register_autosave_widgets()
        
        layout.addWidget(self.tabs)
        
        # 底部按钮
        btn_layout = QHBoxLayout()

        self.save_status_label = QLabel("")
        self.save_status_label.setObjectName("settings_save_status")
        btn_layout.addWidget(self.save_status_label)
        
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

    # —— 可搜索设置目录 ——

    def _build_search_bar(self) -> QWidget:
        box = QGroupBox("设置目录")
        outer = QVBoxLayout(box)
        outer.setSpacing(8)

        row = QHBoxLayout()
        self.settings_search_input = QLineEdit()
        self.settings_search_input.setPlaceholderText("搜索设置，例如「主题」「API Key」「重命名」…")
        self.settings_search_input.setClearButtonEnabled(True)
        self.settings_search_input.textChanged.connect(self._on_settings_search)
        row.addWidget(self.settings_search_input, 1)
        outer.addLayout(row)

        cat_row = QHBoxLayout()
        cat_row.setSpacing(6)
        cat_row.addWidget(QLabel("分类:"))
        for cat in list_categories():
            btn = QPushButton(cat.title)
            btn.setToolTip(cat.description)
            btn.clicked.connect(
                lambda _checked=False, cid=cat.id: self._on_category_jump(cid)
            )
            cat_row.addWidget(btn)
        cat_row.addStretch()
        outer.addLayout(cat_row)

        self.settings_search_results = QListWidget()
        self.settings_search_results.setObjectName("settings_search_results")
        self.settings_search_results.setMaximumHeight(140)
        self.settings_search_results.itemActivated.connect(self._on_search_result_activated)
        self.settings_search_results.itemClicked.connect(self._on_search_result_activated)
        self.settings_search_results.hide()
        outer.addWidget(self.settings_search_results)
        return box

    def _on_settings_search(self, text: str) -> None:
        query = (text or "").strip()
        if not query:
            self.settings_search_results.hide()
            self.settings_search_results.clear()
            return
        matches = search_entries(query)
        self.settings_search_results.clear()
        if not matches:
            item = QListWidgetItem("未找到匹配设置")
            item.setFlags(Qt.NoItemFlags)
            self.settings_search_results.addItem(item)
        else:
            for m in matches[:40]:
                item = QListWidgetItem(
                    f"[{m.category.title}] {m.entry.label}"
                    f"    — {m.entry.effect}"
                )
                item.setData(Qt.UserRole, m.entry.widget_key)
                self.settings_search_results.addItem(item)
        self.settings_search_results.show()

    def _on_search_result_activated(self, item: QListWidgetItem) -> None:
        widget_key = item.data(Qt.UserRole)
        if widget_key:
            self.focus_setting(widget_key)

    def _on_category_jump(self, category_id: str) -> None:
        matches = [m for m in search_entries("") if m.category.id == category_id]
        if not matches:
            return
        first = matches[0]
        self.tabs.setCurrentIndex(first.entry.tab_index)
        self.focus_setting(first.entry.widget_key)

    def register_setting_widget(self, widget_key: str, widget) -> None:
        """登记设置控件，供搜索定位与内联保存状态展示。"""
        if widget is None:
            return
        self._search_widget_map[widget_key] = widget
        if hasattr(widget, "setProperty"):
            widget.setProperty("settings_widget_key", widget_key)
        if hasattr(widget, "setToolTip"):
            from gui.models.settings_catalog import get_entry_by_widget

            entry = get_entry_by_widget(widget_key)
            if entry is not None:
                widget.setToolTip(entry.effect)

    def _accumulate_ancestors(self, widget, seen, visited) -> None:
        node = widget
        while node is not None and id(node) not in visited:
            visited.add(id(node))
            seen.append(node)
            node = node.parentWidget()

    def _accumulate_descendants(self, widget, seen, visited) -> None:
        stack = [widget]
        while stack:
            node = stack.pop()
            if node is None or id(node) in visited:
                continue
            visited.add(id(node))
            seen.append(node)
            children = getattr(node, "children", None)
            if callable(children):
                stack.extend(
                    [c for c in children() if isinstance(c, QWidget)]
                )

    def _bind_setting_target(self, widget, target) -> None:
        """给注册的持久化控件绑定变更信号，使其防抖自动保存。

        兼容复合控件：容器（如缩略图尺寸的 QHBoxLayout 包装）会向上找到
        所属页签容器、向下收集内部控件，避免因信号判断过严而漏绑。
        """
        seen = []
        visited = set()

        target_widget = self._as_widget(target)
        if target_widget is None:
            return
        self._accumulate_ancestors(target_widget, seen, visited)
        # 布局本身不是 QWidget，向下收集要基于其父容器；
        # 用独立 visited2 避免根节点被祖先遍历占用而跳过子树。
        self._accumulate_descendants(target_widget, seen, set())
        for node in seen:
            node.installEventFilter(self)
            self._connect_autosave_signals(node)

    @staticmethod
    def _as_widget(obj):
        """把布局或控件统一解析成 QWidget。"""
        if isinstance(obj, QWidget):
            return obj
        parent = getattr(obj, "parentWidget", None)
        if callable(parent):
            return parent()
        parent = getattr(obj, "parent", None)
        if callable(parent):
            resolved = parent()
            if isinstance(resolved, QWidget):
                return resolved
        return None

    def _connect_autosave_signals(self, widget) -> None:
        if getattr(widget, "_settings_autosave_bound", False):
            return
        bound = False
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(self._on_pref_changed)
            bound = True
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(self._on_pref_changed)
            bound = True
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self._on_pref_changed)
            bound = True
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(self._on_pref_changed)
            bound = True
        elif isinstance(widget, QPlainTextEdit):
            widget.textChanged.connect(self._on_pref_changed)
            bound = True
        elif isinstance(widget, QListWidget):
            widget.currentRowChanged.connect(self._on_pref_changed)
            bound = True
        if bound:
            widget._settings_autosave_bound = True

    def register_autosave_widgets(self) -> None:
        """为「普通界面偏好」控件登记并绑定防抖自动保存。"""
        for entry in search_entries(""):
            if entry.entry.save_mode != AUTOSAVE:
                continue
            widget = self._search_widget_map.get(entry.entry.widget_key)
            if widget is not None:
                self._bind_setting_target(widget, widget)

    def focus_setting(self, widget_key: str) -> bool:
        """跳到并高亮某个设置项。返回是否找到。"""
        widget = self._search_widget_map.get(widget_key)
        if widget is None:
            return False
        from gui.models.settings_catalog import get_entry_by_widget

        entry = get_entry_by_widget(widget_key)
        if entry is not None:
            self.tabs.setCurrentIndex(entry.tab_index)
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget)
                break
            parent = parent.parentWidget()
        widget.setFocus(Qt.OtherFocusReason)
        widgets = [widget] + self._collect_child_widgets(widget)
        for node in widgets:
            if node.property("settings_highlight"):
                node.setProperty("settings_highlight", False)
                node.style().unpolish(node)
                node.style().polish(node)
        for node in widgets:
            if hasattr(node, "setProperty"):
                node.setProperty("settings_highlight", True)
                node.style().unpolish(node)
                node.style().polish(node)
        return True

    def _collect_child_widgets(self, widget) -> list:
        found = []
        stack = [widget]
        while stack:
            node = stack.pop()
            children = getattr(node, "children", None)
            if callable(children):
                for child in children():
                    if isinstance(child, QWidget):
                        found.append(child)
                        stack.append(child)
        return found

    def _on_pref_changed(self, *_args) -> None:
        self.prefs_controller.schedule_save(self._collect_autosave_prefs)

    def _collect_autosave_prefs(self) -> None:
        """把普通界面偏好控件当前值同步进 settings（内存）。

        只覆盖 AUTOSAVE 条目；不写入任何敏感/路由/导出键。
        """
        from gui.models.settings_catalog import AUTOSAVE, search_entries

        for match in search_entries(""):
            entry = match.entry
            if entry.save_mode != AUTOSAVE:
                continue
            widget = self._search_widget_map.get(entry.widget_key)
            if widget is None:
                continue
            value = self._read_widget_value(entry.widget_key, widget)
            if value is None:
                continue
            SettingsManager.update_setting(self.settings, entry.key, value)

        prefs = self.settings.setdefault("ui_preferences", {})
        if prefs.get("remember_work_scope"):
            prefs["last_work_scope"] = self.service.get_work_scope_paths()
            prefs["last_work_scope_exclusions"] = list(
                getattr(self.service, "_work_scope_exclusions", set())
            )

    def _on_preferences_saved(self, message: str) -> None:
        self._set_save_status(message)
        self.ui_preferences_applied.emit()

    def _read_widget_value(self, widget_key: str, widget):
        try:
            if widget_key == "thumbnail_size":
                w = getattr(self, "thumb_w_spin", None)
                h = getattr(self, "thumb_h_spin", None)
                if w is None or h is None:
                    return None
                return [int(w.value()), int(h.value())]
            if isinstance(widget, QSpinBox):
                return int(widget.value())
            if isinstance(widget, QCheckBox):
                return bool(widget.isChecked())
            if isinstance(widget, QComboBox):
                return widget.currentData() or widget.currentText()
            if isinstance(widget, QLineEdit):
                return widget.text()
            if isinstance(widget, QPlainTextEdit):
                return widget.toPlainText()
        except Exception:
            return None
        return None

    def _set_save_status(self, text: str, error: bool = False) -> None:
        if not hasattr(self, "save_status_label"):
            return
        self.save_status_label.setText(text)
        color = "#f48771" if error else "#89d185"
        self.save_status_label.setStyleSheet(
            f"color: {color}; font-size: 12px;"
        )

    def _on_rename_changed(self) -> None:
        """重命名模式的唯一真实入口：同步到导出页签控件。"""
        if hasattr(self, "filename_tmpl") and hasattr(self, "pattern_input"):
            if self.filename_tmpl.text() != self.pattern_input.text():
                self.filename_tmpl.setText(self.pattern_input.text())
        self.prefs_controller.schedule_save(
            lambda: SettingsManager.update_setting(
                self.settings, "rename_pattern", self.pattern_input.text().strip()
            )
        )

    def eventFilter(self, obj, event):
        if event.type() in (
            event.Type.FocusIn,
            event.Type.MouseButtonPress,
            event.Type.Enter,
        ):
            widget_key = None
            if hasattr(obj, "property"):
                widget_key = obj.property("settings_widget_key")
            if not widget_key and hasattr(obj, "objectName"):
                widget_key = obj.objectName()
            entry = None
            if widget_key:
                from gui.models.settings_catalog import get_entry_by_widget

                entry = get_entry_by_widget(widget_key)
            if entry is not None:
                if event.type() == event.Type.FocusIn:
                    self._set_save_status(f"{entry.label}：{entry.effect}")
                self._highlight_catalog_entry(entry.widget_key)
        return super().eventFilter(obj, event)

    def _highlight_catalog_entry(self, widget_key: str) -> None:
        results = getattr(self, "settings_search_results", None)
        if results is None or not results.isVisible():
            return
        for row in range(results.count()):
            item = results.item(row)
            if item.data(Qt.UserRole) == widget_key:
                results.setCurrentItem(item)
                break

    # —— 页签 ——

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
        self.workers_spin.setRange(1, 32)
        self.workers_spin.setValue(
            int(SettingsManager.get_setting(self.settings, "processing.max_workers", 16) or 16)
        )
        self.workers_spin.setToolTip(
            "分析管线并发（等 AI 的任务数）。过大不会加快抽帧，反而卡死整机。建议 8～16，硬顶 32。"
        )

        self.extract_parallel_spin = QSpinBox()
        self.extract_parallel_spin.setRange(1, 16)
        self.extract_parallel_spin.setValue(
            int(
                SettingsManager.get_setting(
                    self.settings, "processing.extract_parallel", 4
                )
                or 4
            )
        )
        self.extract_parallel_spin.setToolTip(
            "同时 OpenCV 解码抽帧的路数。4K 素材建议 2～4；过大必导致整机/GUI 卡死。"
        )

        self.extract_workers_spin = QSpinBox()
        self.extract_workers_spin.setRange(1, 32)
        self.extract_workers_spin.setValue(
            int(
                SettingsManager.get_setting(
                    self.settings, "processing.extract_workers", 8
                )
                or 8
            )
        )
        self.extract_workers_spin.setToolTip(
            "工作范围入库时缩略图抽帧并发。建议 4～8。"
        )
        
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

        self.auto_xmp_cb = QCheckBox("分析成功后自动同步 XMP 侧车（大批量建议关闭）")
        self.auto_xmp_cb.setToolTip(
            "开启后每成功一条就写 .xmp，大批量分析会显著拖慢并加重卡顿。建议关，分析完再手动同步。"
        )
        self.auto_xmp_cb.setChecked(
            bool(SettingsManager.get_setting(
                self.settings, "processing.auto_sync_xmp_after_analysis", False
            ))
        )
        
        proc_form.addRow("分析管线并发:", self.workers_spin)
        proc_form.addRow("同时抽帧路数:", self.extract_parallel_spin)
        proc_form.addRow("入库抽帧并发:", self.extract_workers_spin)
        proc_form.addRow("AI 分析抽帧数:", self.frames_spin)
        proc_form.addRow("JPEG 压缩质量:", self.quality_spin)
        proc_form.addRow(self.scene_detect_cb)
        proc_form.addRow(self.audio_cb)
        proc_form.addRow(self.auto_xmp_cb)
        form.addRow(proc_group)

        # 分析重试（高级，ADR-0005）
        retry_group = QGroupBox("分析重试（高级）")
        retry_form = QFormLayout(retry_group)
        retry_hint = QLabel(
            "自动重试可提高成功率，但会增加 API 费用。批次补跑仅对失败项再跑 1 轮。"
        )
        retry_hint.setWordWrap(True)
        retry_hint.setStyleSheet("color: #888; font-size: 12px;")
        self.call_extra_spin = QSpinBox()
        self.call_extra_spin.setRange(0, 5)
        self.call_extra_spin.setValue(
            int(SettingsManager.get_setting(
                self.settings, "processing.analysis_retry.call_extra_attempts", 2
            ))
        )
        self.call_extra_spin.setToolTip("同一次 AI 请求额外重试次数（默认 2，共 3 次尝试）")
        self.item_max_spin = QSpinBox()
        self.item_max_spin.setRange(1, 5)
        self.item_max_spin.setValue(
            int(SettingsManager.get_setting(
                self.settings, "processing.analysis_retry.item_max_attempts", 2
            ))
        )
        self.item_max_spin.setToolTip("单条视频完整流程最多尝试次数（默认 2）")
        self.batch_rerun_cb = QCheckBox("首轮结束后自动补跑失败项（1 轮）")
        self.batch_rerun_cb.setChecked(
            bool(SettingsManager.get_setting(
                self.settings, "processing.analysis_retry.batch_rerun_enabled", True
            ))
        )
        retry_form.addRow(retry_hint)
        retry_form.addRow("调用层额外重试:", self.call_extra_spin)
        retry_form.addRow("单条最大尝试:", self.item_max_spin)
        retry_form.addRow(self.batch_rerun_cb)
        form.addRow(retry_group)
        
        # 自动重命名规则：重命名模式的唯一真实入口（导出页签仅同步展示）
        rename_group = QGroupBox("自动重命名规则")
        rename_form = QFormLayout(rename_group)
        
        self.pattern_input = QLineEdit(SettingsManager.get_setting(self.settings, "rename_pattern", ""))
        self.pattern_input.setPlaceholderText("{category}-{tags}-{summary}-{original_name}")
        self.pattern_input.textChanged.connect(self._on_rename_changed)
        
        rename_form.addRow("命名模式:", self.pattern_input)
        rename_form.addRow(QLabel("可用变量: {category}, {tags}, {summary}, {original_name}"))
        form.addRow(rename_group)
        
        scroll.setWidget(container)
        layout.addWidget(scroll)

        if hasattr(self, "thumb_w_spin"):
            self.register_setting_widget("thumbnail_size", self.thumb_w_spin)
        self.register_setting_widget("workers", self.workers_spin)
        self.register_setting_widget("extract_parallel", self.extract_parallel_spin)
        self.register_setting_widget("extract_workers", self.extract_workers_spin)
        self.register_setting_widget("frames", self.frames_spin)
        self.register_setting_widget("quality", self.quality_spin)
        self.register_setting_widget("scene_detect", self.scene_detect_cb)
        self.register_setting_widget("audio", self.audio_cb)
        self.register_setting_widget("auto_xmp", self.auto_xmp_cb)
        self.register_setting_widget("call_extra", self.call_extra_spin)
        self.register_setting_widget("item_max", self.item_max_spin)
        self.register_setting_widget("batch_rerun", self.batch_rerun_cb)
        self.register_setting_widget("rename_pattern", self.pattern_input)
        return tab

    def create_ai_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        ensure_providers(self.settings)

        # 敏感配置：保留明确保存区提示
        hint = QLabel(
            "以上均为敏感配置，修改后需点底部「保存所有设置」才会落盘并重建 AI 客户端。"
            "供应商档案只保存显示名、API Key、Base URL（最多 5 个）。"
            "「当前供应商」是未单独配置任务时的默认回退。"
            "下方「任务模型路由」可为每个 AI 功能跨供应商选模型。"
            "进行中的分析本轮不中断。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(hint)

        # —— 供应商列表 ——
        list_group = QGroupBox("模型供应商（最多 5 个）")
        list_layout = QVBoxLayout(list_group)

        row = QHBoxLayout()
        self.provider_list = QListWidget()
        self.provider_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.provider_list.setMinimumHeight(120)
        self.provider_list.currentItemChanged.connect(self._on_provider_selection_changed)
        row.addWidget(self.provider_list, 1)

        side = QVBoxLayout()
        self.set_current_btn = QPushButton("设为当前")
        self.set_current_btn.clicked.connect(self._on_set_current_provider)
        self.add_provider_btn = QPushButton("新增")
        self.add_provider_btn.clicked.connect(self._on_add_provider)
        self.del_provider_btn = QPushButton("删除")
        self.del_provider_btn.clicked.connect(self._on_delete_provider)
        side.addWidget(self.set_current_btn)
        side.addWidget(self.add_provider_btn)
        side.addWidget(self.del_provider_btn)
        side.addStretch()
        row.addLayout(side)
        list_layout.addLayout(row)

        self.current_provider_label = QLabel("")
        self.current_provider_label.setStyleSheet("color: #ce9178;")
        list_layout.addWidget(self.current_provider_label)
        layout.addWidget(list_group)

        # —— 当前选中档案字段 ——
        form_group = QGroupBox("档案字段")
        form = QFormLayout(form_group)

        self.provider_name_input = QLineEdit()
        self.provider_name_input.setPlaceholderText("显示名，如「默认」「中转A」")
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_url_input = QLineEdit()

        form.addRow("显示名:", self.provider_name_input)
        form.addRow("API Key:", self.api_key_input)
        form.addRow("API Base URL:", self.api_url_input)
        layout.addWidget(form_group)

        model_group = QGroupBox("档案默认模型名（可选，供路由回退）")
        model_form = QFormLayout(model_group)

        self.cls_model_input = QLineEdit()
        self.tag_model_input = QLineEdit()
        self.desc_model_input = QLineEdit()
        self.cls_model_input.setPlaceholderText("如 gemini-2.0-flash")
        self.tag_model_input.setPlaceholderText("如 gemini-2.0-flash")
        self.desc_model_input.setPlaceholderText("如 gemini-2.0-flash")

        model_form.addRow("分类默认:", self.cls_model_input)
        model_form.addRow("标签默认:", self.tag_model_input)
        model_form.addRow("描述默认:", self.desc_model_input)
        layout.addWidget(model_group)

        # —— 任务模型路由六槽 ——
        route_group = QGroupBox("任务模型路由（跨供应商）")
        route_layout = QVBoxLayout(route_group)
        route_hint = QLabel(
            "每个 AI 功能可指定供应商 + 模型名。供应商选「（默认回退）」则使用当前供应商。"
        )
        route_hint.setWordWrap(True)
        route_hint.setStyleSheet("color: #888; font-size: 12px;")
        route_layout.addWidget(route_hint)

        self._route_widgets = {}
        for key in TASK_ROUTE_KEYS:
            row = QHBoxLayout()
            label = QLabel(TASK_ROUTE_LABELS.get(key, key))
            label.setMinimumWidth(100)
            row.addWidget(label)
            prov_combo = QComboBox()
            prov_combo.setMinimumWidth(140)
            model_edit = QLineEdit()
            model_edit.setPlaceholderText("模型名")
            row.addWidget(prov_combo, 1)
            row.addWidget(model_edit, 1)
            route_layout.addLayout(row)
            self._route_widgets[key] = {"provider": prov_combo, "model": model_edit}

        layout.addWidget(route_group)
        layout.addStretch()

        self._provider_ui_ready = True
        self._refresh_provider_list(select_id=self.settings.get("current_provider_id"))
        self._load_task_routes_ui()
        self.register_setting_widget("api_key", self.api_key_input)
        self.register_setting_widget("api_url", self.api_url_input)
        self.register_setting_widget("provider_list", self.provider_list)
        return tab

    def _load_task_routes_ui(self) -> None:
        if not getattr(self, "_route_widgets", None):
            return
        ensure_providers(self.settings)
        providers = list_providers(self.settings)
        routing = get_task_model_routing(self.settings)
        for key, widgets in self._route_widgets.items():
            combo: QComboBox = widgets["provider"]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("（默认回退）", "")
            for p in providers:
                combo.addItem(p.get("display_name") or p["id"], p["id"])
            slot = routing.get(key) or {}
            pid = str(slot.get("provider_id") or "")
            idx = combo.findData(pid)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            widgets["model"].setText(str(slot.get("model") or ""))
            combo.blockSignals(False)
            if key == TASK_ROUTE_KEYS[0]:
                self.register_setting_widget("task_routing", combo)

    def _flush_task_routes_to_settings(self) -> None:
        if not getattr(self, "_route_widgets", None):
            return
        routing = {}
        for key, widgets in self._route_widgets.items():
            combo: QComboBox = widgets["provider"]
            routing[key] = {
                "provider_id": str(combo.currentData() or ""),
                "model": widgets["model"].text().strip(),
            }
        replace_task_model_routing(self.settings, routing)

    def _selected_provider_id(self) -> str:
        item = self.provider_list.currentItem() if hasattr(self, "provider_list") else None
        if not item:
            return ""
        return str(item.data(Qt.UserRole) or "")

    def _flush_provider_form_to_settings(self) -> None:
        """把表单字段写回当前列表选中的档案（内存，未落盘）。"""
        if not self._provider_ui_ready:
            return
        pid = self._selected_provider_id()
        if not pid:
            return
        try:
            update_provider(
                self.settings,
                pid,
                display_name=self.provider_name_input.text().strip(),
                api_key=self.api_key_input.text().strip(),
                base_url=self.api_url_input.text().strip(),
                models={
                    "video_classification": self.cls_model_input.text().strip(),
                    "tag_generation": self.tag_model_input.text().strip(),
                    "content_description": self.desc_model_input.text().strip(),
                },
            )
        except ProviderError:
            pass

    def _load_provider_form(self, provider_id: str) -> None:
        providers = list_providers(self.settings)
        provider = None
        for p in providers:
            if p["id"] == provider_id:
                provider = p
                break
        if provider is None and providers:
            provider = providers[0]
        if provider is None:
            return
        self._suppress_provider_signals = True
        try:
            self.provider_name_input.setText(provider.get("display_name") or "")
            self.api_key_input.setText(provider.get("api_key") or "")
            self.api_url_input.setText(provider.get("base_url") or "")
            models = provider.get("models") or {}
            self.cls_model_input.setText(models.get("video_classification") or "")
            self.tag_model_input.setText(models.get("tag_generation") or "")
            self.desc_model_input.setText(models.get("content_description") or "")
        finally:
            self._suppress_provider_signals = False

    def _refresh_provider_list(self, select_id: str = None) -> None:
        if not hasattr(self, "provider_list"):
            return
        ensure_providers(self.settings)
        providers = list_providers(self.settings)
        current_id = str(self.settings.get("current_provider_id") or "")
        target = select_id or self._selected_provider_id() or current_id
        if target not in {p["id"] for p in providers} and providers:
            target = providers[0]["id"]

        self._suppress_provider_signals = True
        try:
            self.provider_list.clear()
            select_row = 0
            for i, p in enumerate(providers):
                name = p.get("display_name") or "未命名"
                mark = " ★当前" if p["id"] == current_id else ""
                item = QListWidgetItem(f"{name}{mark}")
                item.setData(Qt.UserRole, p["id"])
                self.provider_list.addItem(item)
                if p["id"] == target:
                    select_row = i
            if self.provider_list.count() > 0:
                self.provider_list.setCurrentRow(select_row)
            cur = resolve_current_provider(self.settings)
            self.current_provider_label.setText(
                f"当前供应商：{cur.get('display_name') or cur.get('id')}"
            )
            self.add_provider_btn.setEnabled(len(providers) < MAX_PROVIDERS)
            self.del_provider_btn.setEnabled(len(providers) > 1)
        finally:
            self._suppress_provider_signals = False

        if target:
            self._load_provider_form(target)
        self._load_task_routes_ui()

    def _on_provider_selection_changed(self, current, previous):
        if self._suppress_provider_signals:
            return
        # 先保存离开的那条
        if previous is not None:
            prev_id = str(previous.data(Qt.UserRole) or "")
            if prev_id:
                try:
                    update_provider(
                        self.settings,
                        prev_id,
                        display_name=self.provider_name_input.text().strip(),
                        api_key=self.api_key_input.text().strip(),
                        base_url=self.api_url_input.text().strip(),
                        models={
                            "video_classification": self.cls_model_input.text().strip(),
                            "tag_generation": self.tag_model_input.text().strip(),
                            "content_description": self.desc_model_input.text().strip(),
                        },
                    )
                except ProviderError:
                    pass
        if current is not None:
            self._load_provider_form(str(current.data(Qt.UserRole) or ""))

    def _on_set_current_provider(self):
        self._flush_provider_form_to_settings()
        pid = self._selected_provider_id()
        if not pid:
            return
        try:
            set_current_provider(self.settings, pid)
            self._refresh_provider_list(select_id=pid)
            # 立即写穿并重建 AI 客户端，避免「设为当前」后仍用旧 Key
            try:
                SettingsManager.save_settings(self.settings, self.service.db)
            except Exception:
                pass
            if hasattr(self.service, "reload_ai_from_settings"):
                self.service.reload_ai_from_settings()
            self._set_save_status("已切换当前供应商并保存")
        except ProviderError as e:
            QMessageBox.warning(self, "无法切换", str(e))

    def _on_add_provider(self):
        self._flush_provider_form_to_settings()
        try:
            _, provider = add_provider(
                self.settings,
                display_name=f"供应商{len(list_providers(self.settings)) + 1}",
                set_as_current=False,
            )
            self._refresh_provider_list(select_id=provider["id"])
        except ProviderLimitError as e:
            QMessageBox.warning(self, "已达上限", str(e))
        except ProviderError as e:
            QMessageBox.warning(self, "无法新增", str(e))

    def _on_delete_provider(self):
        self._flush_provider_form_to_settings()
        pid = self._selected_provider_id()
        if not pid:
            return
        providers = list_providers(self.settings)
        if len(providers) <= 1:
            QMessageBox.information(self, "无法删除", "至少保留一个模型供应商档案。")
            return
        name = ""
        for p in providers:
            if p["id"] == pid:
                name = p.get("display_name") or pid
                break
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除供应商「{name}」？若它是当前供应商，将自动切换到另一档案。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            remove_provider(self.settings, pid)
            self._refresh_provider_list()
        except ProviderError as e:
            QMessageBox.warning(self, "无法删除", str(e))

    def create_prompts_tab(self):
        """Prompt 全中心：分析 system + 各组 local + 标签库 AI 模板 + 预览。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setSpacing(12)

        hint = QLabel(
            "<b>全部可编辑 Prompt 中心</b>"
            "<br>① 分析全局角色头 ② 各标签组 local_prompt ③ 标签库 AI 三模板"
            "（待审 / 近义 / 标准词助手）。冷启动模板本轮隐藏。"
            "<br><span style='color:#888;'>旧版 prompts.* 三键已废弃，不再参与分析。</span>"
            "<br><span style='color:#888;'>修改后需点底部「保存所有设置」才会写入；"
            "「刷新预览」只展示当前输入，不会写盘。</span>"
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.RichText)
        vbox.addWidget(hint)

        vbox.addWidget(QLabel("<b>① 全局角色头（tag_config.global_settings.system_prompt）</b>"))
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
            current = default_system
        self.system_prompt_edit.setPlainText(current)
        self.system_prompt_edit.setMinimumHeight(90)
        vbox.addWidget(self.system_prompt_edit)

        # ② 各组 local_prompt
        vbox.addWidget(QLabel("<b>② 各标签组 local_prompt（写入 tag_config 各组 rules）</b>"))
        self._local_prompt_edits = {}
        groups = []
        if hasattr(self.service, "get_group_local_prompts"):
            groups = self.service.get_group_local_prompts() or []
        if not groups:
            for g in (self.service.tag_config or {}).get("tag_groups") or []:
                gid = str(g.get("id") or "").strip()
                if not gid or gid.lower() == "pool":
                    continue
                rules = g.get("rules") if isinstance(g.get("rules"), dict) else {}
                groups.append(
                    {
                        "id": gid,
                        "name": str(g.get("name") or gid),
                        "local_prompt": str(rules.get("local_prompt") or ""),
                    }
                )
        for row in groups:
            gid = row["id"]
            name = row.get("name") or gid
            vbox.addWidget(QLabel(f"· {name}（{gid}）"))
            edit = QPlainTextEdit()
            edit.setPlainText(row.get("local_prompt") or "")
            edit.setMinimumHeight(56)
            edit.setMaximumHeight(100)
            edit.setPlaceholderText(f"组 {gid} 的局部引导词")
            self._local_prompt_edits[gid] = edit
            vbox.addWidget(edit)

        # ③ 标签库 AI 模板
        from core.tag_ai_assist import DEFAULT_TAG_AI_PROMPTS

        vbox.addWidget(QLabel("<b>③ 标签库 AI 模板（settings.tag_ai_prompts）</b>"))
        self._tag_ai_prompt_edits = {}
        tag_ai_labels = {
            "pending_tag_ai": "待审词 AI（含批量分拣）",
            "synonym_audit": "近义巡检",
            "standard_tag_ai": "标准词 AI 助手",
        }
        for key, label in tag_ai_labels.items():
            vbox.addWidget(QLabel(f"· {label}"))
            edit = QPlainTextEdit()
            if hasattr(self.service, "get_tag_ai_prompt"):
                text = self.service.get_tag_ai_prompt(key)
            else:
                text = DEFAULT_TAG_AI_PROMPTS.get(key, "")
            edit.setPlainText(text or "")
            edit.setMinimumHeight(64)
            edit.setMaximumHeight(120)
            self._tag_ai_prompt_edits[key] = edit
            vbox.addWidget(edit)

        # 预览
        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("<b>④ 预览</b>"))
        preview_header.addStretch()
        refresh_btn = QPushButton("刷新预览")
        refresh_btn.clicked.connect(self.refresh_prompt_preview)
        preview_header.addWidget(refresh_btn)
        vbox.addLayout(preview_header)

        self.prompt_preview_edit = QPlainTextEdit()
        self.prompt_preview_edit.setReadOnly(True)
        self.prompt_preview_edit.setMinimumHeight(220)
        self.prompt_preview_edit.setPlaceholderText("分析完整拼装 + 标签库 AI 模板摘要")
        vbox.addWidget(self.prompt_preview_edit)

        note = QLabel(
            "分析预览不含视频帧。标签库 AI 预览为 system 模板正文（user 含动态词表，运行时组装）。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 12px;")
        vbox.addWidget(note)
        vbox.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)
        # 展示当前已保存配置；预览不写入 local_prompt
        self.refresh_prompt_preview()
        self.register_setting_widget("system_prompt", self.system_prompt_edit)
        return tab

    def refresh_prompt_preview(self):
        """分析完整预览 + 标签库 AI 模板摘要。

        只读取当前输入用于展示，**不会**把 local_prompt 写回内存或磁盘，
        避免「刷新预览」意外持久化未保存的编辑。
        """
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
            parts = [
                "======== [分析 system] ========",
                f"{prompts.get('system_prompt', '')}",
                "",
                "======== [分析 user]（另附视频帧） ========",
                f"{prompts.get('user_prompt', '')}",
                "",
                "======== [标签库 AI system 模板] ========",
            ]
            for key, edit in (getattr(self, "_tag_ai_prompt_edits", None) or {}).items():
                parts.append(f"--- {key} ---")
                parts.append(edit.toPlainText().strip() or "（空）")
                parts.append("")
            self.prompt_preview_edit.setPlainText("\n".join(parts))
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

        hint = QLabel("以下为普通界面偏好，修改后会自动保存，无需点「保存所有设置」。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        layout.addRow(hint)
        
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

        self.register_setting_widget("font_size", self.font_spin)
        self.register_setting_widget("thumbnail_size", thumb_layout)
        self.register_setting_widget("theme", self.theme_combo)
        self.register_setting_widget("default_view", self.default_view_combo)
        self.register_setting_widget("sidebar_width", self.sidebar_spin)
        self.register_setting_widget("detail_panel_expanded", self.detail_expanded_cb)
        self.register_setting_widget("remember_work_scope", self.remember_scope_cb)
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
        
        # 1. 重命名模板（镜像常规页签的唯一入口；编辑请到「常规」）
        rename_group = QGroupBox("文件名模板")
        rename_form = QFormLayout(rename_group)
        
        self.filename_tmpl = QLineEdit()
        pattern = SettingsManager.get_setting(self.settings, "rename_pattern", "{category}-{tags}-{summary}-{original_name}")
        self.filename_tmpl.setText(pattern)
        self.filename_tmpl.setReadOnly(True)
        self.filename_tmpl.setToolTip("重命名模式在「常规 → 自动重命名规则」中编辑，这里仅同步展示。")
        
        vars_hint = QLabel("变量: {date}, {category}, {tags}, {summary}, {original_name}")
        vars_hint.setStyleSheet("color: #888; font-size: 11px;")
        mirror_hint = QLabel("编辑入口：常规 → 自动重命名规则（此处仅同步展示实际保存值）。")
        mirror_hint.setStyleSheet("color: #888; font-size: 11px;")
        
        rename_form.addRow("模式:", self.filename_tmpl)
        rename_form.addRow("", vars_hint)
        rename_form.addRow("", mirror_hint)
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

        self.register_setting_widget("rename_pattern", self.filename_tmpl)
        self.register_setting_widget("xmp_lang", self.xmp_lang_combo)
        self.register_setting_widget("xmp_hierarchical", self.xmp_hierarchical_cb)
        self.register_setting_widget("xmp_prefix_category", self.xmp_prefix_cat_cb)
        self.register_setting_widget("ale_columns", self.ale_columns_edit)
        return tab

    def apply_settings(self):
        """同步 UI 数据到 settings 字典并保存（敏感配置的明确保存区）"""
        # 模型供应商：表单 → 档案 → 任务路由 → 写穿扁平 api
        if self._provider_ui_ready:
            self._flush_provider_form_to_settings()
            self._flush_task_routes_to_settings()
            ensure_providers(self.settings)
        
        # Processing
        SettingsManager.update_setting(self.settings, "processing.max_workers", self.workers_spin.value())
        SettingsManager.update_setting(
            self.settings, "processing.extract_parallel", self.extract_parallel_spin.value()
        )
        SettingsManager.update_setting(
            self.settings, "processing.extract_workers", self.extract_workers_spin.value()
        )
        SettingsManager.update_setting(self.settings, "processing.max_frames", self.frames_spin.value())
        SettingsManager.update_setting(self.settings, "processing.jpeg_quality", self.quality_spin.value())
        SettingsManager.update_setting(self.settings, "processing.enable_scene_detection", self.scene_detect_cb.isChecked())
        SettingsManager.update_setting(self.settings, "processing.enable_audio_transcription", self.audio_cb.isChecked())
        SettingsManager.update_setting(
            self.settings,
            "processing.auto_sync_xmp_after_analysis",
            self.auto_xmp_cb.isChecked(),
        )
        if hasattr(self, "call_extra_spin"):
            SettingsManager.update_setting(
                self.settings,
                "processing.analysis_retry.call_extra_attempts",
                self.call_extra_spin.value(),
            )
            SettingsManager.update_setting(
                self.settings,
                "processing.analysis_retry.item_max_attempts",
                self.item_max_spin.value(),
            )
            SettingsManager.update_setting(
                self.settings,
                "processing.analysis_retry.batch_rerun_enabled",
                self.batch_rerun_cb.isChecked(),
            )
            SettingsManager.update_setting(
                self.settings,
                "processing.analysis_retry.batch_rerun_max_rounds",
                1,
            )
        
        # Prompt 全中心：system + local + 标签库 AI 模板；旧 prompts.* 不再写入
        if hasattr(self, "system_prompt_edit"):
            system_prompt = self.system_prompt_edit.toPlainText().strip()
            if hasattr(self.service, "set_system_prompt"):
                self.service.set_system_prompt(system_prompt)
            else:
                self.service.tag_config.setdefault("global_settings", {})["system_prompt"] = system_prompt
                self.service.save_tag_config(self.service.tag_config)
        if getattr(self, "_local_prompt_edits", None):
            for gid, edit in self._local_prompt_edits.items():
                if hasattr(self.service, "set_group_local_prompt"):
                    self.service.set_group_local_prompt(gid, edit.toPlainText())
        if getattr(self, "_tag_ai_prompt_edits", None):
            for key, edit in self._tag_ai_prompt_edits.items():
                if hasattr(self.service, "set_tag_ai_prompt"):
                    self.service.set_tag_ai_prompt(key, edit.toPlainText())
        
        # UI & Others（普通偏好此处兜底，防抖自动保存已覆盖）
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
        
        # V6.0 Export Templates：重命名模式以「常规」页签入口为准
        if hasattr(self, "pattern_input"):
            SettingsManager.update_setting(self.settings, "rename_pattern", self.pattern_input.text().strip())
        SettingsManager.update_setting(self.settings, "global_settings.language", self.xmp_lang_combo.currentText())
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.xmp_hierarchical", self.xmp_hierarchical_cb.isChecked())
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.xmp_prefix_category", self.xmp_prefix_cat_cb.isChecked())
        
        ale_cols = [c.strip() for c in self.ale_columns_edit.text().split(",") if c.strip()]
        SettingsManager.update_setting(self.settings, "global_settings.export_schemes.ale_columns", ale_cols)
        
        try:
            # 始终写回 service 持有的 settings 引用
            self.settings = self.service.settings
            SettingsManager.save_settings(self.settings, self.service.db)
            # 立即重建 AI 客户端，使 Key / Base URL / 模型配置当场生效
            if hasattr(self.service, "reload_ai_from_settings"):
                self.service.reload_ai_from_settings()
            if self._provider_ui_ready:
                self._refresh_provider_list(select_id=self._selected_provider_id())
                self._load_task_routes_ui()
            self._set_save_status("所有设置已保存并已应用")
            QMessageBox.information(self, "成功", "设置已保存并已应用到 AI 引擎，界面偏好将尽量立即生效。")
            self.settings_applied.emit()
        except Exception as e:
            self._set_save_status(f"保存失败：{e}", error=True)
            QMessageBox.critical(self, "错误", f"保存设置失败: {e}")

    def reset_to_defaults(self):
        reply = QMessageBox.question(self, "确认", "确定要恢复所有设置为默认值吗？这将覆盖当前配置。",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            import copy
            from core.video_organizer_service import DEFAULT_SETTINGS
            # 原地更新 service.settings，保持引用一致
            defaults = copy.deepcopy(DEFAULT_SETTINGS)
            ensure_providers(defaults)
            self.service.settings.clear()
            self.service.settings.update(defaults)
            self.settings = self.service.settings
            # 重建 UI 控件以反映默认值
            # 清空 tabs 后重建
            while self.tabs.count():
                w = self.tabs.widget(0)
                self.tabs.removeTab(0)
                if w:
                    w.deleteLater()
            self._provider_ui_ready = False
            self._search_widget_map = {}
            self.tabs.addTab(self.create_general_tab(), "常规")
            self.tabs.addTab(self.create_ai_tab(), "AI 引擎")
            self.tabs.addTab(self.create_prompts_tab(), "Prompt 模板")
            self.tabs.addTab(self.create_interface_tab(), "界面")
            self.tabs.addTab(self.create_export_tab(), "导出模板")
            self.register_autosave_widgets()
            self.register_setting_widget("rename_pattern", self.pattern_input)
            QMessageBox.information(self, "已重置", "设置已重置为默认值，请点击「保存所有设置」以写入磁盘。")
