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
from ..workers.io_worker import run_io_job


CATEGORY_TAB_INDEX = {
    "analysis": 0,
    "ai": 1,
    "prompts": 2,
    "everyday": 3,
    "naming": 4,
}


class SettingsView(QWidget):
    """设置视图 - 可搜索目录 + 按用户任务分类导航的高级设置系统"""
    settings_applied = Signal()
    ui_preferences_applied = Signal()  # 普通偏好应用不触发 AI 客户端重建。
    interface_layout_reset = Signal()  # 完整布局重置成功后通知主窗口刷新现存列表列宽。

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
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        
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
        # 页面通过设置目录的任务分类切换，隐藏重复的传统页签导航。
        self.tabs.tabBar().hide()

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
        self.tabs.currentChanged.connect(self._update_save_button_label)
        self.tabs.currentChanged.connect(self._sync_category_navigation)
        self._sync_category_navigation(self.tabs.currentIndex())
        
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
        self._category_buttons = {}
        for cat in list_categories():
            btn = QPushButton(cat.title)
            btn.setCheckable(True)
            btn.setToolTip(cat.description)
            btn.clicked.connect(
                lambda _checked=False, cid=cat.id: self._on_category_jump(cid)
            )
            self._category_buttons[cat.id] = btn
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
        tab_index = CATEGORY_TAB_INDEX.get(category_id)
        if tab_index is None or tab_index >= self.tabs.count():
            return
        self.tabs.setCurrentIndex(tab_index)
        self._sync_category_navigation(tab_index)

    def _sync_category_navigation(self, tab_index: int) -> None:
        active_category = next(
            (
                category_id
                for category_id, mapped_index in CATEGORY_TAB_INDEX.items()
                if mapped_index == tab_index
            ),
            None,
        )
        for category_id, button in getattr(self, "_category_buttons", {}).items():
            button.setChecked(category_id == active_category)

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

    def _update_save_button_label(self, index: int) -> None:
        if hasattr(self, "save_btn"):
            self.save_btn.setText("保存 AI 设置" if index == 1 else "保存所有设置")

    def _set_save_status(self, text: str, error: bool = False) -> None:
        if not hasattr(self, "save_status_label"):
            return
        self.save_status_label.setText(text)
        color = "#f48771" if error else "#89d185"
        self.save_status_label.setStyleSheet(
            f"color: {color}; font-size: 12px;"
        )

    def _on_rename_changed(self) -> None:
        """保留重命名模式草稿，等待用户显式保存。"""
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
        return tab

    def create_ai_tab(self):
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        outer_layout.setContentsMargins(12, 8, 12, 8)

        import copy
        self._ai_draft_settings = copy.deepcopy(self.settings)
        ensure_providers(self._ai_draft_settings)
        self._ai_test_status = {}

        intro = QLabel(
            "为每项 AI 功能选择连接档案和模型。未指定档案的任务使用当前供应商；"
            "编辑后点击底部「保存所有设置」，新请求使用新配置，进行中的分析不受影响。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888; font-size: 12px;")
        outer_layout.addWidget(intro)

        self.ai_save_state_label = QLabel("设置已保存")
        self.ai_save_state_label.setObjectName("ai_save_state")
        self.effective_config_label = QLabel()
        self.effective_config_label.setWordWrap(True)
        self.effective_config_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.effective_config_label.setStyleSheet("font-size: 12px; color: #888;")
        outer_layout.addWidget(self.ai_save_state_label)
        outer_layout.addWidget(self.effective_config_label)

        self.ai_sections = QTabWidget()
        self.ai_sections.setObjectName("ai_settings_sections")
        self.ai_sections.addTab(self._build_provider_settings_page(), "供应商档案")
        self.ai_sections.addTab(self._build_task_routing_page(), "任务模型路由")
        outer_layout.addWidget(self.ai_sections, 1)

        self._provider_ui_ready = True
        self._refresh_provider_list(select_id=self._ai_draft_settings.get("current_provider_id"))
        self._load_task_routes_ui()
        self._refresh_effective_config_summary()
        self._set_ai_dirty(False)
        self.register_setting_widget("api_key", self.api_key_input)
        self.register_setting_widget("api_url", self.api_url_input)
        self.register_setting_widget("provider_list", self.provider_list)
        return tab

    def _build_provider_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)

        list_group = QGroupBox("模型供应商（最多 5 个）")
        list_layout = QVBoxLayout(list_group)
        row = QHBoxLayout()
        self.provider_list = QListWidget()
        self.provider_list.setObjectName("provider_list")
        self.provider_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.provider_list.setMinimumHeight(105)
        self.provider_list.currentItemChanged.connect(self._on_provider_selection_changed)
        row.addWidget(self.provider_list, 1)

        actions = QVBoxLayout()
        self.set_current_btn = QPushButton("设为当前")
        self.set_current_btn.setToolTip("仅作为未单独指定供应商的任务的默认回退")
        self.set_current_btn.clicked.connect(self._on_set_current_provider)
        self.add_provider_btn = QPushButton("新增档案")
        self.add_provider_btn.clicked.connect(self._on_add_provider)
        self.del_provider_btn = QPushButton("删除档案")
        self.del_provider_btn.clicked.connect(self._on_delete_provider)
        for button in (self.set_current_btn, self.add_provider_btn, self.del_provider_btn):
            actions.addWidget(button)
        actions.addStretch()
        row.addLayout(actions)
        list_layout.addLayout(row)
        layout.addWidget(list_group)

        connection_group = QGroupBox("连接信息")
        connection_form = QFormLayout(connection_group)
        self.provider_name_input = QLineEdit()
        self.provider_name_input.setPlaceholderText("例如：默认、OpenRouter、局域网模型")
        self.api_key_input = QLineEdit()
        self.api_key_input.setObjectName("provider_api_key")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_url_input = QLineEdit()
        self.api_url_input.setPlaceholderText("例如：https://api.example.com/v1")
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItem("Chat Completions", "chat_completions")
        self.protocol_combo.addItem("Responses API", "responses")
        self.api_key_reveal_btn = QPushButton("显示")
        self.api_key_reveal_btn.setCheckable(True)
        self.api_key_reveal_btn.setFixedWidth(72)
        self.api_key_reveal_btn.toggled.connect(self._toggle_api_key_visibility)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_input, 1)
        key_row.addWidget(self.api_key_reveal_btn)
        connection_form.addRow("显示名:", self.provider_name_input)
        connection_form.addRow("API Key:", key_row)
        connection_form.addRow("API Base URL:", self.api_url_input)
        connection_form.addRow("API 协议:", self.protocol_combo)
        layout.addWidget(connection_group)

        self.provider_default_model_group = QGroupBox("档案默认模型（高级回退，可选）")
        model_group = self.provider_default_model_group
        model_group.setCheckable(True)
        model_group.setChecked(False)
        model_layout = QVBoxLayout(model_group)
        model_content = QWidget(model_group)
        model_form = QFormLayout(model_content)
        self.cls_model_input = QLineEdit()
        self.tag_model_input = QLineEdit()
        self.desc_model_input = QLineEdit()
        self.cls_model_input.setPlaceholderText("留空时使用对应任务路由模型")
        self.tag_model_input.setPlaceholderText("留空时使用对应任务路由模型")
        self.desc_model_input.setPlaceholderText("留空时使用对应任务路由模型")
        model_form.addRow("视频分类:", self.cls_model_input)
        model_form.addRow("标签生成:", self.tag_model_input)
        model_form.addRow("内容描述:", self.desc_model_input)
        model_layout.addWidget(model_content)
        model_content.setVisible(False)
        model_group.toggled.connect(model_content.setVisible)
        layout.addWidget(model_group)

        hint = QLabel(
            "档案保存连接凭据。普通任务请在“任务模型路由”中设置模型；此处默认模型仅作未填写任务模型时的回退。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(hint)
        layout.addStretch()

        for widget in (
            self.provider_name_input, self.api_key_input, self.api_url_input,
            self.cls_model_input, self.tag_model_input, self.desc_model_input,
        ):
            widget.textChanged.connect(self._on_ai_settings_edited)
        self.protocol_combo.currentIndexChanged.connect(self._on_ai_settings_edited)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _build_task_routing_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 10, 8, 8)
        hint = QLabel(
            "每项功能可单独指定供应商和模型。选择「默认回退」时使用当前供应商；"
            "模型留空时使用该供应商对应的默认模型。可在本行直接测试当前编辑配置。"
            "测试会发送真实的极小推理请求，可能产生少量费用。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        outer.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        self._route_widgets = {}
        for key in TASK_ROUTE_KEYS:
            group = QGroupBox(TASK_ROUTE_LABELS.get(key, key))
            group.setObjectName(f"route_{key}")
            group_layout = QVBoxLayout(group)
            fields = QHBoxLayout()
            provider_combo = QComboBox()
            provider_combo.setMinimumWidth(130)
            provider_combo.setToolTip("选择此功能使用的供应商；默认回退使用当前供应商")
            model_edit = QLineEdit()
            model_edit.setPlaceholderText("模型名；留空使用供应商默认模型")
            fetch_button = QPushButton("获取模型")
            fetch_button.setToolTip("从此任务最终解析到的供应商获取模型目录")
            test_button = QPushButton("测试此任务")
            test_button.setMinimumWidth(100)
            fields.addWidget(provider_combo, 1)
            fields.addWidget(model_edit, 1)
            fields.addWidget(fetch_button)
            fields.addWidget(test_button)
            group_layout.addLayout(fields)

            resolved_label = QLabel()
            resolved_label.setWordWrap(True)
            resolved_label.setStyleSheet("color: #888; font-size: 12px;")
            group_layout.addWidget(resolved_label)
            status_label = QLabel("尚未测试")
            status_label.setWordWrap(True)
            status_label.setStyleSheet("color: #888; font-size: 12px;")
            group_layout.addWidget(status_label)
            layout.addWidget(group)

            provider_combo.currentIndexChanged.connect(self._on_route_widget_changed)
            model_edit.textChanged.connect(self._on_route_widget_changed)
            fetch_button.clicked.connect(
                lambda _checked=False, task=key: self._fetch_task_models(task)
            )
            test_button.clicked.connect(
                lambda _checked=False, task=key: self._test_task_connection(task)
            )
            self._route_widgets[key] = {
                "provider": provider_combo,
                "model": model_edit,
                "fetch": fetch_button,
                "test": test_button,
                "resolved": resolved_label,
                "status": status_label,
            }
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return page

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.api_key_input.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.api_key_reveal_btn.setText("隐藏" if visible else "显示")

    def _set_ai_dirty(self, dirty: bool = True) -> None:
        self._ai_config_dirty = bool(dirty)
        label = getattr(self, "ai_save_state_label", None)
        if label is None:
            return
        if dirty:
            label.setText("有未保存的 AI 设置更改；连接测试不会写入配置文件。")
            label.setStyleSheet("color: #d7ba7d; font-size: 12px;")
        else:
            label.setText("AI 设置已保存；新配置用于后续请求，进行中的分析不受影响。")
            label.setStyleSheet("color: #89d185; font-size: 12px;")

    def _on_ai_settings_edited(self, *_args) -> None:
        if not getattr(self, "_provider_ui_ready", False) or self._suppress_provider_signals:
            return
        self._set_ai_dirty(True)
        self._clear_ai_test_states()
        self._flush_provider_form_to_settings()
        self._refresh_effective_config_summary()

    def _clear_ai_test_states(self) -> None:
        for widgets in (getattr(self, "_route_widgets", {}) or {}).values():
            status = widgets.get("status")
            if status is not None:
                status.setText("尚未测试（配置已更改）")
                status.setStyleSheet("color: #888; font-size: 12px;")
        self._ai_test_status = {}

    def _refresh_effective_config_summary(self) -> None:
        """Show effective provider and task routes without credentials."""
        if not hasattr(self, "effective_config_label"):
            return
        try:
            effective_settings = self._build_ai_draft_settings()
            current = resolve_current_provider(effective_settings)
            current_name = current.get("display_name") or current.get("id") or "未命名"
            key_state = "已配置 API Key" if str(current.get("api_key") or "").strip() else "缺少 API Key"
            url_state = "已配置 Base URL" if str(current.get("base_url") or "").strip() else "缺少 Base URL"
            routing = get_task_model_routing(effective_settings)
            explicit_count = sum(
                1 for key in TASK_ROUTE_KEYS
                if str((routing.get(key) or {}).get("provider_id") or "").strip()
            )
            self.effective_config_label.setText(
                f"当前供应商：{current_name}（{key_state}，{url_state}）；"
                f"已单独指定供应商的任务：{explicit_count}/{len(TASK_ROUTE_KEYS)}。"
                "各任务实际使用的供应商和模型显示在对应路由项下。"
            )
            for key in TASK_ROUTE_KEYS:
                route = resolve_task_route(effective_settings, key)
                provider = route.get("display_name") or route.get("provider_id") or "未命名"
                model = route.get("model") or "未设置模型"
                widgets = (getattr(self, "_route_widgets", {}) or {}).get(key)
                if widgets:
                    selected = str(widgets["provider"].currentData() or "")
                    widgets["resolved"].setText(
                        f"实际使用：{provider} / {model}"
                        + ("（使用当前供应商回退）" if not selected else "")
                    )
        except Exception as exc:
            self.effective_config_label.setText(f"\u5f53\u524d\u914d\u7f6e\u6682\u65f6\u65e0\u6cd5\u8bfb\u53d6\uff1a{exc}")

    def _fetch_task_models(self, task_key: str) -> None:
        """Fetch the resolved provider's model catalog; only update the draft field."""
        if not getattr(self, "_provider_ui_ready", False):
            return
        self._flush_provider_form_to_settings()
        self._flush_task_routes_to_settings()
        draft_settings = self._build_ai_draft_settings()
        route = resolve_task_route(draft_settings, task_key)
        widgets = self._route_widgets[task_key]
        widgets["fetch"].setEnabled(False)

        def on_ok(models):
            widgets["fetch"].setEnabled(True)
            if not models:
                QMessageBox.information(self, "\u6a21\u578b\u5217\u8868", "\u670d\u52a1\u7aef\u672a\u8fd4\u56de\u6a21\u578b\uff1b\u4ecd\u53ef\u624b\u52a8\u8f93\u5165\u6a21\u578b ID\u3002")
                return
            from PySide6.QtWidgets import QInputDialog
            selected, accepted = QInputDialog.getItem(
                self, "\u9009\u62e9\u6a21\u578b",
                f"{route.get('display_name') or 'Provider'} / {route.get('api_protocol') or 'chat_completions'}",
                models, 0, True,
            )
            if accepted and selected.strip():
                widgets["model"].setText(selected.strip())

        def on_fail(message):
            widgets["fetch"].setEnabled(True)
            safe_message = str(message)
            for provider in list_providers(draft_settings):
                key = str(provider.get("api_key") or "").strip()
                if key:
                    safe_message = safe_message.replace(key, "[\u5df2\u9690\u85cf]")
            QMessageBox.warning(self, "\u83b7\u53d6\u6a21\u578b\u5931\u8d25", f"{safe_message}\\n\\n\u53ef\u7ee7\u7eed\u624b\u52a8\u8f93\u5165\u6a21\u578b ID\u3002")

        run_io_job(
            self,
            fn=lambda: self.service.list_ai_models(task_key, draft_settings),
            on_ok=on_ok,
            on_fail=on_fail,
            busy_message="Fetching model catalog…",
        )

    def _test_task_connection(self, task_key: str) -> None:
        """测试当前界面编辑的指定路由；只更新运行态，不落盘。"""
        if not getattr(self, "_provider_ui_ready", False):
            return
        draft_settings = self._build_ai_draft_settings()
        widgets = self._route_widgets[task_key]
        widgets["test"].setEnabled(False)
        widgets["status"].setText("测试中…")
        widgets["status"].setStyleSheet("color: #888; font-size: 12px;")
        tested_fingerprint = self._task_route_fingerprint(task_key, draft_settings)

        def on_ok(result):
            widgets["test"].setEnabled(True)
            if tested_fingerprint != self._task_route_fingerprint(task_key, self._build_ai_draft_settings()):
                widgets["status"].setText("测试结果已过期；配置有更改，请重新测试。")
                widgets["status"].setStyleSheet("color: #d7ba7d; font-size: 12px;")
                return
            name = result.get("display_name") or result.get("provider_id") or "未命名"
            model = result.get("model") or "未设置模型"
            protocol = result.get("api_protocol") or "chat_completions"
            widgets["status"].setText(f"连接成功：{name} / {protocol} / {model}；测试调用可能产生少量费用。")
            widgets["status"].setStyleSheet("color: #89d185; font-size: 12px;")
            self._ai_test_status[task_key] = (tested_fingerprint, "success")

        def on_fail(message):
            widgets["test"].setEnabled(True)
            if tested_fingerprint != self._task_route_fingerprint(task_key, self._build_ai_draft_settings()):
                widgets["status"].setText("测试结果已过期；配置有更改，请重新测试。")
                widgets["status"].setStyleSheet("color: #d7ba7d; font-size: 12px;")
                return
            safe_message = str(message)
            for provider in list_providers(draft_settings):
                key = str(provider.get("api_key") or "").strip()
                if key:
                    safe_message = safe_message.replace(key, "[已隐藏]")
            widgets["status"].setText(f"连接失败：{safe_message}")
            widgets["status"].setStyleSheet("color: #f48771; font-size: 12px;")
            self._ai_test_status[task_key] = (tested_fingerprint, "failure")

        run_io_job(
            self,
            fn=lambda: self.service.test_ai_connection(task_key, draft_settings),
            on_ok=on_ok,
            on_fail=on_fail,
            busy_message=f"正在测试“{TASK_ROUTE_LABELS.get(task_key, task_key)}”连接…",
        )

    def _build_ai_draft_settings(self) -> dict:
        """把界面编辑值应用到隔离副本，供连接测试使用。"""
        import copy

        draft_settings = copy.deepcopy(self._ai_draft_settings)
        provider_id = self._selected_provider_id()
        draft_provider_ids = {provider["id"] for provider in list_providers(draft_settings)}
        if provider_id and provider_id in draft_provider_ids:
            update_provider(
                draft_settings,
                provider_id,
                display_name=self.provider_name_input.text().strip(),
                api_key=self.api_key_input.text().strip(),
                base_url=self.api_url_input.text().strip(),
                api_protocol=str(self.protocol_combo.currentData() or "chat_completions"),
                models={
                    "video_classification": self.cls_model_input.text().strip(),
                    "tag_generation": self.tag_model_input.text().strip(),
                    "content_description": self.desc_model_input.text().strip(),
                },
            )
        routes = {
            key: {
                "provider_id": str(widgets["provider"].currentData() or ""),
                "model": widgets["model"].text().strip(),
            }
            for key, widgets in self._route_widgets.items()
        }
        replace_task_model_routing(draft_settings, routes)
        ensure_providers(draft_settings)
        return draft_settings

    def _task_route_fingerprint(self, task_key: str, settings=None) -> tuple:
        route = resolve_task_route(settings or self._ai_draft_settings, task_key)
        return (route.get("provider_id"), route.get("model"), route.get("base_url"), route.get("api_key"), route.get("api_protocol"))

    def _on_route_widget_changed(self, *_args) -> None:
        if not getattr(self, "_provider_ui_ready", False):
            return
        self._flush_task_routes_to_settings()
        self._set_ai_dirty(True)
        self._clear_ai_test_states()
        self._refresh_effective_config_summary()

    def _load_task_routes_ui(self) -> None:
        if not getattr(self, "_route_widgets", None):
            return
        ensure_providers(self._ai_draft_settings)
        providers = list_providers(self._ai_draft_settings)
        routing = get_task_model_routing(self._ai_draft_settings)
        for key, widgets in self._route_widgets.items():
            combo: QComboBox = widgets["provider"]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("默认回退", "")
            for provider in providers:
                combo.addItem(provider.get("display_name") or provider["id"], provider["id"])
            slot = routing.get(key) or {}
            index = combo.findData(str(slot.get("provider_id") or ""))
            combo.setCurrentIndex(index if index >= 0 else 0)
            widgets["model"].blockSignals(True)
            widgets["model"].setText(str(slot.get("model") or ""))
            widgets["model"].blockSignals(False)
            combo.blockSignals(False)
            if key == TASK_ROUTE_KEYS[0]:
                self.register_setting_widget("task_routing", combo)
        self._refresh_effective_config_summary()

    def _flush_task_routes_to_settings(self) -> None:
        if not getattr(self, "_route_widgets", None):
            return
        routing = {}
        for key, widgets in self._route_widgets.items():
            combo = widgets["provider"]
            routing[key] = {"provider_id": str(combo.currentData() or ""), "model": widgets["model"].text().strip()}
        replace_task_model_routing(self._ai_draft_settings, routing)

    def _selected_provider_id(self) -> str:
        item = self.provider_list.currentItem() if hasattr(self, "provider_list") else None
        if not item:
            return ""
        return str(item.data(Qt.UserRole) or "")

    def _flush_provider_form_to_settings(self) -> None:
        """把表单字段写回 AI 草稿设置，等待显式保存。"""
        if not self._provider_ui_ready:
            return
        provider_id = self._selected_provider_id()
        if not provider_id:
            return
        try:
            update_provider(
                self._ai_draft_settings,
                provider_id,
                display_name=self.provider_name_input.text().strip(),
                api_key=self.api_key_input.text().strip(),
                base_url=self.api_url_input.text().strip(),
                api_protocol=str(self.protocol_combo.currentData() or "chat_completions"),
                models={
                    "video_classification": self.cls_model_input.text().strip(),
                    "tag_generation": self.tag_model_input.text().strip(),
                    "content_description": self.desc_model_input.text().strip(),
                },
            )
        except ProviderError:
            pass

    def _load_provider_form(self, provider_id: str) -> None:
        providers = list_providers(self._ai_draft_settings)
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
            protocol_index = self.protocol_combo.findData(provider.get("api_protocol") or "chat_completions")
            self.protocol_combo.setCurrentIndex(protocol_index if protocol_index >= 0 else 0)
            models = provider.get("models") or {}
            self.cls_model_input.setText(models.get("video_classification") or "")
            self.tag_model_input.setText(models.get("tag_generation") or "")
            self.desc_model_input.setText(models.get("content_description") or "")
        finally:
            self._suppress_provider_signals = False

    def _refresh_provider_list(self, select_id: str = None) -> None:
        if not hasattr(self, "provider_list"):
            return
        ensure_providers(self._ai_draft_settings)
        providers = list_providers(self._ai_draft_settings)
        current_id = str(self._ai_draft_settings.get("current_provider_id") or "")
        routed_counts = {}
        routing = get_task_model_routing(self._ai_draft_settings)
        for task_key in TASK_ROUTE_KEYS:
            slot = routing.get(task_key) or {}
            routed_id = str(slot.get("provider_id") or "")
            if routed_id:
                routed_counts[routed_id] = routed_counts.get(routed_id, 0) + 1
        target = select_id or self._selected_provider_id() or current_id
        if target not in {p["id"] for p in providers} and providers:
            target = providers[0]["id"]

        self._suppress_provider_signals = True
        try:
            self.provider_list.clear()
            select_row = 0
            for i, p in enumerate(providers):
                name = p.get("display_name") or "未命名"
                marks = []
                if p["id"] == current_id:
                    marks.append("当前")
                route_count = routed_counts.get(p["id"], 0)
                if route_count:
                    marks.append(f"{route_count} 项任务")
                suffix = f" · {' · '.join(marks)}" if marks else ""
                item = QListWidgetItem(f"{name}{suffix}")
                item.setData(Qt.UserRole, p["id"])
                self.provider_list.addItem(item)
                if p["id"] == target:
                    select_row = i
            if self.provider_list.count() > 0:
                self.provider_list.setCurrentRow(select_row)
            self.add_provider_btn.setEnabled(len(providers) < MAX_PROVIDERS)
            self.del_provider_btn.setEnabled(len(providers) > 1)
        finally:
            self._suppress_provider_signals = False

        if target:
            self._load_provider_form(target)
        self._load_task_routes_ui()
        self._refresh_effective_config_summary()

    def _on_provider_selection_changed(self, current, previous):
        if self._suppress_provider_signals:
            return
        if previous is not None:
            previous_id = str(previous.data(Qt.UserRole) or "")
            if previous_id:
                try:
                    update_provider(
                        self._ai_draft_settings,
                        previous_id,
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
            self._refresh_effective_config_summary()

    def _on_set_current_provider(self):
        self._flush_provider_form_to_settings()
        provider_id = self._selected_provider_id()
        if not provider_id:
            return
        try:
            set_current_provider(self._ai_draft_settings, provider_id)
            self._set_ai_dirty(True)
            self._clear_ai_test_states()
            self._refresh_provider_list(select_id=provider_id)
            self._refresh_effective_config_summary()
        except ProviderError as error:
            QMessageBox.warning(self, "无法切换", str(error))

    def _apply_ai_draft_to_settings(self, target: dict) -> None:
        """只合并 AI 连接配置，避免草稿覆盖其他页面的最新偏好。"""
        import copy

        draft = self._ai_draft_settings
        target["model_providers"] = copy.deepcopy(draft.get("model_providers") or [])
        target["current_provider_id"] = str(draft.get("current_provider_id") or "")
        target["task_model_routing"] = copy.deepcopy(draft.get("task_model_routing") or {})
        target_api = target.setdefault("api", {})
        draft_api = draft.get("api") or {}
        for key in ("key", "base_url", "model_personalization"):
            if key in draft_api:
                target_api[key] = copy.deepcopy(draft_api[key])
        ensure_providers(target)

    def _save_ai_settings(self) -> None:
        import copy

        self._flush_provider_form_to_settings()
        self._flush_task_routes_to_settings()
        try:
            candidate = copy.deepcopy(self.settings)
            self._apply_ai_draft_to_settings(candidate)
            # 先持久化候选值；失败时活动设置与 AI 客户端保持原样。
            SettingsManager.save_settings(candidate, self.service.db)
            self.settings.clear()
            self.settings.update(candidate)
            if hasattr(self.service, "reload_ai_from_settings"):
                self.service.reload_ai_from_settings()
            self._ai_draft_settings = copy.deepcopy(self.settings)
            ensure_providers(self._ai_draft_settings)
            self._refresh_provider_list(select_id=self._selected_provider_id())
            self._set_ai_dirty(False)
            self._set_save_status("AI 设置已保存并应用；新配置用于后续请求。")
            self.settings_applied.emit()
        except Exception as exc:
            self._set_ai_dirty(True)
            self._set_save_status(f"AI 设置保存失败：{exc}", error=True)
            QMessageBox.critical(self, "AI 设置保存失败", str(exc))

    def _on_add_provider(self):
        self._flush_provider_form_to_settings()
        try:
            _, provider = add_provider(
                self._ai_draft_settings,
                display_name=f"供应商{len(list_providers(self._ai_draft_settings)) + 1}",
                set_as_current=False,
            )
            self._refresh_provider_list(select_id=provider["id"])
            self._set_ai_dirty(True)
            self._clear_ai_test_states()
        except ProviderLimitError as e:
            QMessageBox.warning(self, "已达上限", str(e))
        except ProviderError as e:
            QMessageBox.warning(self, "无法新增", str(e))

    def _on_delete_provider(self):
        self._flush_provider_form_to_settings()
        pid = self._selected_provider_id()
        if not pid:
            return
        providers = list_providers(self._ai_draft_settings)
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
            remove_provider(self._ai_draft_settings, pid)
            self._refresh_provider_list()
            self._set_ai_dirty(True)
            self._clear_ai_test_states()
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

        self.reset_layout_btn = QPushButton("恢复默认界面布局")
        self.reset_layout_btn.setToolTip("恢复字体、缩略图、主题、默认视图、侧边栏和详情面板布局；不删除工作范围。")
        self.reset_layout_btn.clicked.connect(self._reset_interface_layout)
        layout.addRow(self.reset_layout_btn)
        
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
        
        # 1. 自动重命名规则
        rename_group = QGroupBox("自动重命名规则")
        rename_form = QFormLayout(rename_group)

        pattern = SettingsManager.get_setting(
            self.settings,
            "rename_pattern",
            "{category}-{tags}-{summary}-{original_name}",
        )
        self.pattern_input = QLineEdit(pattern)
        self.filename_tmpl = self.pattern_input  # 兼容旧调用方；不创建第二个镜像控件。
        self.pattern_input.setPlaceholderText("{category}-{tags}-{summary}-{original_name}")
        self.pattern_input.textChanged.connect(self._on_rename_changed)

        vars_hint = QLabel("可用变量: {date}, {category}, {tags}, {summary}, {original_name}")
        vars_hint.setStyleSheet("color: #888; font-size: 11px;")
        rename_form.addRow("命名模式:", self.pattern_input)
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

        self.register_setting_widget("rename_pattern", self.filename_tmpl)
        self.register_setting_widget("xmp_lang", self.xmp_lang_combo)
        self.register_setting_widget("xmp_hierarchical", self.xmp_hierarchical_cb)
        self.register_setting_widget("xmp_prefix_category", self.xmp_prefix_cat_cb)
        self.register_setting_widget("ale_columns", self.ale_columns_edit)
        return tab

    def _reset_interface_layout(self) -> None:
        reply = QMessageBox.question(
            self,
            "恢复默认界面布局",
            "恢复字体、缩略图、主题、默认视图、侧边栏和详情面板布局？\n工作范围和标签数据不会被删除。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        import copy
        from core.video_organizer_service import DEFAULT_SETTINGS

        defaults = copy.deepcopy(DEFAULT_SETTINGS.get("ui_preferences") or {})
        prefs = self.settings.setdefault("ui_preferences", {})
        for key in (
            "font_size",
            "thumbnail_size",
            "theme",
            "default_view",
            "sidebar_width",
            "detail_panel_expanded",
        ):
            if key in defaults:
                prefs[key] = defaults[key]
        prefs.pop("table_columns_visible", None)
        prefs.pop("table_column_widths", None)

        widgets = (
            self.font_spin,
            self.thumb_w_spin,
            self.thumb_h_spin,
            self.theme_combo,
            self.default_view_combo,
            self.sidebar_spin,
            self.detail_expanded_cb,
        )
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.font_spin.setValue(int(defaults.get("font_size", 14)))
            thumb = defaults.get("thumbnail_size", [160, 90])
            self.thumb_w_spin.setValue(int(thumb[0]))
            self.thumb_h_spin.setValue(int(thumb[1]))
            self.theme_combo.setCurrentIndex(0 if defaults.get("theme", "dark") == "dark" else 1)
            self.default_view_combo.setCurrentIndex(0 if defaults.get("default_view", "list") != "card" else 1)
            self.sidebar_spin.setValue(int(defaults.get("sidebar_width", 220)))
            self.detail_expanded_cb.setChecked(bool(defaults.get("detail_panel_expanded", True)))
        finally:
            for widget in widgets:
                widget.blockSignals(False)

        try:
            SettingsManager.save_settings(self.settings, self.service.db)
            self._set_save_status("默认界面布局已恢复")
            self.ui_preferences_applied.emit()
            self.interface_layout_reset.emit()
        except Exception as exc:
            self._set_save_status(f"恢复布局失败：{exc}", error=True)
            QMessageBox.critical(self, "错误", f"恢复默认界面布局失败：{exc}")

    def apply_settings(self):
        """同步 UI 数据并保存；AI 页的按钮仅保存 AI 配置。"""
        import copy

        if getattr(self, "_provider_ui_ready", False) and self.tabs.currentIndex() == 1:
            self._save_ai_settings()
            return
        # 模型供应商：表单 → 档案 → 任务路由 → 写穿扁平 api
        if self._provider_ui_ready:
            self._flush_provider_form_to_settings()
            self._flush_task_routes_to_settings()
            self._apply_ai_draft_to_settings(self.settings)
        
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
                self._ai_draft_settings = copy.deepcopy(self.settings)
                ensure_providers(self._ai_draft_settings)
                self._refresh_provider_list(select_id=self._selected_provider_id())
                self._load_task_routes_ui()
                self._set_ai_dirty(False)
            self._refresh_effective_config_summary()
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
