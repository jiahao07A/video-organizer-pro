# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWidget, QApplication, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QFormLayout, QGroupBox, QLineEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QMessageBox, QPlainTextEdit,
    QComboBox, QListWidget, QListWidgetItem, QAbstractItemView, QFileDialog,
    QProgressDialog, QSizePolicy, QCompleter, QListView, QStackedWidget,
)
from PySide6.QtCore import QObject, QThread, QThreadPool, Signal, Qt, QTimer, QModelIndex
import threading
from PySide6.QtGui import QStandardItemModel, QStandardItem
from core.video_organizer_service import VideoOrganizerService, SettingsManager
from core.data_directory import DataDirectoryError
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
    search_entries,
)
from gui.services.settings_controller import SettingsController
from ..workers.io_worker import run_io_job
from gui.widgets.model_picker import ModelPicker


class SettingsPageStack(QStackedWidget):
    """用堆叠页面替代顶部页签，同时保留旧搜索代码需要的少量接口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = []

    def addTab(self, widget, label):
        self._labels.append(str(label))
        return self.addWidget(widget)

    def removeTab(self, index):
        widget = self.widget(index)
        if widget is not None:
            self.removeWidget(widget)
            widget.deleteLater()
        if 0 <= index < len(self._labels):
            self._labels.pop(index)

    def tabText(self, index):
        return self._labels[index] if 0 <= index < len(self._labels) else ""

    def insertTab(self, index, widget, label):
        self._labels.insert(index, str(label))
        return self.insertWidget(index, widget)


def _window_has_active_background_work(window) -> bool:
    monitor = getattr(window, "media_monitor", None)
    if monitor is not None and monitor.analysis_running:
        return True
    widgets = [window, *window.findChildren(QWidget)]
    if any(thread.isRunning() for thread in window.findChildren(QThread)):
        return True
    for widget in widgets:
        for value in vars(widget).values():
            if isinstance(value, threading.Thread) and value.is_alive():
                return True
            if isinstance(value, QThread) and value.isRunning():
                return True
            if isinstance(value, QThreadPool) and value.activeThreadCount() > 0:
                return True
            if isinstance(value, QObject):
                try:
                    nested_values = vars(value).values()
                except TypeError:
                    continue
                if any(
                    isinstance(nested, QThread) and nested.isRunning()
                    for nested in nested_values
                ):
                    return True
                try:
                    nested_values = vars(value).values()
                except TypeError:
                    continue
                if any(
                    isinstance(nested, QThreadPool)
                    and nested.activeThreadCount() > 0
                    for nested in nested_values
                ):
                    return True
    return False


class SettingsView(QWidget):
    """设置视图 - 可搜索的分页设置界面"""
    settings_applied = Signal()
    ui_preferences_applied = Signal()  # 普通偏好应用不触发 AI 客户端重建。
    interface_layout_reset = Signal()  # 完整布局重置成功后通知主窗口刷新现存列表列宽。
    media_watch_changed = Signal()

    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self._data_directory_switch_enabled = not bool(
            getattr(service, "manual_test_environment", False)
        )
        self.settings = service.settings
        self._provider_ui_ready = False
        self._suppress_provider_signals = False
        self._applied_ai_configuration = None
        # 普通界面偏好：防抖自动保存并显示状态
        self.prefs_controller = SettingsController(service, parent=self)
        self.prefs_controller.register_target(
            "tag_config", "提示词与导出配置", lambda: self.service.tag_config,
            lambda: self.service.save_tag_config(self.service.tag_config),
        )
        self.prefs_controller.pending.connect(
            lambda: self._set_save_status("有改动待保存…")
        )
        self.prefs_controller.saved.connect(self._on_preferences_saved)
        self._target_states = {}
        self.prefs_controller.target_changed.connect(self._on_target_state)
        self.prefs_controller.save_failed.connect(
            lambda msg: self._set_save_status(msg, error=True)
        )
        self._search_scroll_timer = QTimer(self)
        self._search_scroll_timer.setSingleShot(True)
        self._search_scroll_timer.timeout.connect(self._finish_search_focus)
        self._search_widget_map = {}
        self.setup_ui()
        self._manual_dirty = False
        self._bind_manual_dirty()
        self._applied_ai_configuration = self._ai_configuration()
        import copy
        self._applied_watch_configuration = copy.deepcopy(self.settings.get("processing", {}).get("media_watch", {}))

    def _ai_configuration(self):
        import copy
        return copy.deepcopy({key: self.settings.get(key) for key in
                              ("api", "model_providers", "current_provider_id", "task_model_routing")})

    def _bind_manual_dirty(self):
        def dirty(*_args):
            if self._suppress_provider_signals:
                return
            self._manual_dirty = True
            self._on_pref_changed()
        for widget in self.tabs.findChildren(QWidget):
            # 软件更新面板有自己的保存协议，不参与设置页自动保存。
            node = widget
            is_update = False
            while node is not None:
                if node.objectName() == "software_update_panel":
                    is_update = True
                    break
                node = node.parentWidget()
            if is_update or widget is getattr(self, "model_provider_combo", None) or widget.property("settings_view_only") or getattr(widget, "_update_dirty_bound", False) or getattr(widget, "_settings_autosave_bound", False):
                continue
            signal = None
            if isinstance(widget, QLineEdit):
                signal = widget.textEdited
            elif isinstance(widget, QPlainTextEdit) and not widget.isReadOnly():
                signal = widget.textChanged
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                signal = widget.valueChanged
            elif isinstance(widget, QCheckBox):
                signal = widget.toggled
            elif isinstance(widget, QComboBox):
                signal = widget.currentIndexChanged
            elif hasattr(widget, "textChanged"):
                signal = widget.textChanged
            if signal is not None:
                signal.connect(dirty)
                widget._update_dirty_bound = True
                widget.installEventFilter(self)

    def _create_backup_card(self):
        group = QGroupBox("备份与恢复")
        layout = QVBoxLayout(group)
        hint = QLabel("手动创建一致性管理数据备份；恢复会覆盖管理数据并需要重新启动。原始视频文件不参与备份。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        backup = QPushButton("创建管理数据备份")
        backup.clicked.connect(self._backup_configuration)
        layout.addWidget(backup)
        restore = QPushButton("从备份恢复…")
        restore.clicked.connect(self._restore_configuration)
        layout.addWidget(restore)
        return group

    def _backup_configuration(self):
        if self._has_running_background_work() or not self.prefs_controller.flush_now():
            QMessageBox.warning(self, "暂时无法备份", "请先结束后台任务并确认设置保存成功。")
            return
        try:
            restart_monitor = self._stop_monitor_for_maintenance()
        except (RuntimeError, TimeoutError):
            QMessageBox.warning(self, "暂时无法备份", "素材监控仍在处理，请等待任务结束后重试。")
            return
        try:
            path = self.service.backup_configuration()
        finally:
            restart_monitor()
        if path:
            QMessageBox.information(self, "备份完成", f"备份位置：{path}")
        else:
            QMessageBox.warning(self, "备份失败", "未能创建备份，请检查数据目录是否可写。")

    def _restore_configuration(self):
        from PySide6.QtWidgets import QInputDialog
        if self._has_running_background_work() or not self.prefs_controller.flush_now():
            QMessageBox.warning(self, "暂时无法恢复", "请先结束后台任务并确认设置保存成功。")
            return
        try:
            restart_monitor = self._stop_monitor_for_maintenance()
        except (RuntimeError, TimeoutError):
            QMessageBox.warning(self, "暂时无法恢复", "素材监控仍在处理，请等待任务结束后重试。")
            return
        restored = False
        try:
            choices = self.service.list_backups()
            if not choices:
                QMessageBox.information(self, "没有可用备份", "请先创建管理数据备份。")
                return
            chosen, ok = QInputDialog.getItem(self, "选择备份", "恢复后需要重新启动", choices, 0, False)
            if not ok:
                return
            if QMessageBox.question(self, "确认恢复管理数据", "恢复将覆盖当前数据库与配置；操作前会保留安全快照。继续？",
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            restored = self.service.restore_configuration(chosen)
            if restored:
                self.prefs_controller.cancel()
                QMessageBox.information(self, "恢复完成", "请重新启动应用使用恢复后的数据。")
                QApplication.instance().quit()
            else:
                QMessageBox.warning(self, "恢复失败", "恢复未完成，请保留当前数据并检查备份。")
        finally:
            # 恢复成功后不能让旧服务向已恢复的数据库继续写入。
            if not restored:
                restart_monitor()

    def _stop_monitor_for_maintenance(self):
        """停止扫描并复查分析队列；返回恢复原监控运行状态的回调。"""
        monitor = getattr(self.window(), "media_monitor", None)
        if monitor is None:
            return lambda: None
        if monitor.analysis_running:
            raise RuntimeError("monitor busy")
        thread = getattr(monitor, "_thread", None)
        started = thread is not None and thread.is_alive()
        paused = monitor._paused_all
        monitor.stop()

        def restart():
            if started:
                monitor.start(paused=paused)

        observer = getattr(monitor, "_observer", None)
        if any(worker is not None and worker.is_alive() for worker in (thread, observer)):
            raise RuntimeError("monitor did not stop")
        if monitor.analysis_running:
            restart()
            raise RuntimeError("monitor busy")
        return restart

    def create_update_tab(self):
        from gui.services.update_controller import UpdateController
        from gui.widgets.software_update import SoftwareUpdatePanel
        controller = getattr(self.service, "update_controller", None)
        if controller is None:
            controller = UpdateController(self.service, self)
            self.service.update_controller = controller
        def install():
            callback = getattr(self.window(), "install_update", None)
            if callback:
                callback()
            else:
                QMessageBox.warning(self, "软件更新", "请从完整应用主窗口执行安装")
        self.update_panel = SoftwareUpdatePanel(controller, install, self)
        self.register_setting_widget("software_update", self.update_panel)
        return self.update_panel

    def save_before_update(self):
        return self.prefs_controller.flush()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        title = QLabel("系统设置")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #ce9178;")
        layout.addWidget(title)

        # 设置搜索：保留全局搜索，同时用左侧任务导航替代顶部页签。
        layout.addWidget(self._build_search_bar())

        self.tabs = SettingsPageStack()
        self.tabs.setObjectName("settings_tabs")

        general_page = self.create_general_tab()
        ai_connections_page, ai_models_page = self.create_ai_tab()
        export_page = self.create_export_tab()
        data_group = self._take_settings_group(general_page, "管理数据目录")
        analysis_group = self._take_settings_group(general_page, "视频分析工作流")
        failure_group = self._take_settings_group(general_page, "分析失败处理")
        rename_group = self._take_settings_group(general_page, "自动重命名模板")
        general_page.deleteLater()
        self._remove_settings_group(export_page, "文件名模板")
        from .media_watch_settings import MediaWatchSettingsView
        self.media_watch_page = MediaWatchSettingsView(self.service, self, mode="global")
        self.media_watch_page.changed.connect(self._on_watch_global_changed)
        for key, widget in self.media_watch_page.fields.items():
            self.register_setting_widget(f"watch_{key}", widget)
        self._watch_global_defaults = self.media_watch_page

        page_widgets = {
            "everyday": self.create_interface_tab(),
            "ai_connections": ai_connections_page,
            "ai_models": ai_models_page,
            "prompts": self.create_prompts_tab(),
            "video_analysis": self._compose_settings_page(
                "视频分析", "控制分析任务、图像准备和音频转录。", [analysis_group],
            ),
            "failure_handling": self._compose_settings_page(
                "失败处理", "普通重试与 HTTP 400 恢复是两套独立策略。", [failure_group],
            ),
            "monitoring": self.media_watch_page,
            "naming": self._compose_settings_page(
                "命名与导出", "模板只决定后续操作，不会自动重命名或导出。",
                [rename_group, *export_page.findChildren(QGroupBox)],
            ),
            "system": self._compose_settings_page(
                "数据与维护", "管理设置与数据库所在目录、备份和软件更新。",
                [data_group, self._create_backup_card(), self.create_update_tab()],
            ),
        }
        self._rebuild_responsibility_cards(page_widgets)
        for page in page_widgets.values():
            for label in page.findChildren(QLabel):
                label.setWordWrap(True)
                label.setMinimumWidth(0)
                label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            for form in page.findChildren(QFormLayout):
                form.setRowWrapPolicy(QFormLayout.WrapAllRows)
                form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                form.setFormAlignment(Qt.AlignTop)
                form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
            for group in page.findChildren(QGroupBox):
                group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._page_index_by_id = {}
        self._page_description_by_id = {}
        for category in __import__("gui.models.settings_catalog", fromlist=["CATEGORIES"]).CATEGORIES:
            page = page_widgets[category.id]
            self._page_index_by_id[category.id] = self.tabs.addTab(page, category.title)
            self._page_description_by_id[category.id] = category.description

        self.settings_nav = QListWidget()
        self.settings_nav.setObjectName("settings_navigation")
        self.settings_nav.setFixedWidth(205)
        self.settings_nav.setSpacing(4)
        self.settings_nav.setSelectionMode(QAbstractItemView.SingleSelection)
        self.settings_nav.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._nav_row_to_page = {}
        last_group = None
        for category in __import__("gui.models.settings_catalog", fromlist=["CATEGORIES"]).CATEGORIES:
            if category.group != last_group:
                heading = QListWidgetItem(category.group)
                heading.setFlags(Qt.NoItemFlags)
                heading.setData(Qt.UserRole, None)
                heading.setForeground(Qt.gray)
                self.settings_nav.addItem(heading)
                last_group = category.group
            page_index = self._page_index_by_id[category.id]
            item = QListWidgetItem("   " + category.title)
            item.setToolTip(category.description)
            item.setData(Qt.UserRole, page_index)
            self._nav_row_to_page[self.settings_nav.count()] = page_index
            self.settings_nav.addItem(item)
        self.settings_nav.currentRowChanged.connect(self._on_settings_nav_row_changed)
        self.tabs.currentChanged.connect(self._sync_settings_nav)
        self._sync_settings_nav(0)

        content_row = QHBoxLayout()
        content_row.setSpacing(14)
        content_row.addWidget(self.settings_nav)
        content_row.addWidget(self.tabs, 1)
        layout.addLayout(content_row, 1)

        # 所有页面建立后，统一绑定自动保存。
        self.register_autosave_widgets()
        self._install_card_reset_buttons()

        # 底部按钮
        btn_layout = QHBoxLayout()

        self.save_status_label = QLabel("")
        self.save_status_label.setObjectName("settings_save_status")
        self.save_status_label.setWordWrap(True)
        self.save_status_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        status_box = QVBoxLayout()
        status_box.addWidget(self.save_status_label)
        self.target_status_label = QLabel("设置文件 · 提示词与导出配置")
        self.target_status_label.setStyleSheet("color: #888; font-size: 11px;")
        status_box.addWidget(self.target_status_label)
        btn_layout.addLayout(status_box, 1)

        self.save_btn = QPushButton("重试保存")
        self.save_btn.setObjectName("settings_retry_save")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setFixedWidth(100)
        self.save_btn.setVisible(False)
        self.save_btn.clicked.connect(self.prefs_controller.flush)

        self.reset_btn = QPushButton("恢复全部默认值")
        self.reset_btn.setFixedHeight(32)
        self.reset_btn.setFixedWidth(145)
        self.reset_btn.clicked.connect(self.reset_to_defaults)

        btn_layout.addStretch()
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.reset_btn)
        layout.addLayout(btn_layout)
        from gui.services.settings_search import build_search_index
        self._ui_search_entries = build_search_index(self)

    def _rebuild_responsibility_cards(self, pages):
        from gui.models.settings_catalog import SETTINGS_ENTRIES
        titles = {
            "appearance": "外观", "material_display": "素材显示", "workspace_layout": "工作区布局",
            "work_scope": "启动时的工作范围", "analysis_pipeline": "整条视频分析流程",
            "analysis_preparation": "分析图片准备", "ingestion": "新素材入库",
            "audio_transcription": "音频转录", "analysis_prompt": "分析角色提示",
            "group_prompts": "标签组引导词", "tag_ai_prompts": "标签库 AI 模板",
            "rename_template": "重命名模板", "xmp_export": "XMP 写入与导出", "ale_export": "ALE 导出字段",
        }
        for page_id in ("everyday", "video_analysis", "prompts", "naming"):
            old = pages[page_id]
            grouped = {}
            for entry in SETTINGS_ENTRIES:
                if entry.page_id == page_id and entry.widget_key in self._search_widget_map:
                    grouped.setdefault(entry.card_id, []).append(entry)
            cards = []
            for card_id, entries in grouped.items():
                card = QGroupBox(titles.get(card_id, card_id))
                card.setProperty("settings_card_id", card_id)
                card.setProperty("settings_reset_group", entries[0].reset_group)
                card_layout = QVBoxLayout(card)
                if all(entry.advanced for entry in entries):
                    card.setCheckable(True)
                    card.setChecked(False)
                    warning = QLabel("高级选项：仅影响本卡片说明的处理范围；提高并行度或重试次数可能增加资源占用与请求费用。")
                    warning.setWordWrap(True)
                    card_layout.addWidget(warning)
                advanced_group = None
                if any(entry.advanced for entry in entries) and not all(entry.advanced for entry in entries):
                    advanced_group = QGroupBox("高级选项")
                    advanced_group.setCheckable(True)
                    advanced_group.setChecked(False)
                    advanced_layout = QVBoxLayout(advanced_group)
                    advanced_group.toggled.connect(lambda checked, group=advanced_group: self._set_group_content_visible(group, checked))
                for entry in entries:
                    widget = self._search_widget_map[entry.widget_key]
                    if entry.widget_key == "thumbnail_size":
                        holder = QWidget()
                        size_row = QHBoxLayout(holder)
                        size_row.setContentsMargins(0, 0, 0, 0)
                        size_row.addWidget(self.thumb_w_spin)
                        size_row.addWidget(QLabel("×"))
                        size_row.addWidget(self.thumb_h_spin)
                        size_row.addWidget(QLabel("像素"))
                        widget = holder
                    elif entry.widget_key in {"local_prompt", "tag_ai_prompts"}:
                        edits = self._local_prompt_edits if entry.widget_key == "local_prompt" else self._tag_ai_prompt_edits
                        holder = QWidget()
                        edit_layout = QVBoxLayout(holder)
                        for name, edit in edits.items():
                            names = {str(group.get("id")): str(group.get("name") or group.get("id"))
                                     for group in self.service.tag_config.get("tag_groups", [])}
                            names.update(TASK_ROUTE_LABELS)
                            edit_layout.addWidget(QLabel(names.get(str(name), str(name))))
                            edit_layout.addWidget(edit)
                        widget = holder
                    row = QWidget()
                    row_layout = QVBoxLayout(row)
                    row_layout.setContentsMargins(0, 6, 0, 6)
                    label = QLabel(entry.label + "  ·  " + entry.effective_when)
                    label.setStyleSheet("font-weight: 600;")
                    description = QLabel(entry.effect)
                    description.setWordWrap(True)
                    description.setStyleSheet("color: #888; font-size: 12px;")
                    row_layout.addWidget(label)
                    row_layout.addWidget(description)
                    if isinstance(widget, QSpinBox):
                        widget.setSuffix(" %" if entry.widget_key == "quality" else " 帧" if entry.widget_key == "frames" else " 路" if entry.widget_key in {"workers", "extract_parallel", "extract_workers"} else "")
                        range_hint = QLabel(f"范围：{widget.minimum()}–{widget.maximum()}")
                        range_hint.setStyleSheet("color: #888; font-size: 11px;")
                        row_layout.addWidget(range_hint)
                    widget.setParent(row)
                    row_layout.addWidget(widget)
                    if entry.widget_key == "rename_pattern":
                        row_layout.addWidget(QLabel("可用变量：{date}、{category}、{tags}、{summary}、{original_name}"))
                    if entry.advanced and advanced_group is not None:
                        advanced_layout.addWidget(row)
                    else:
                        card_layout.addWidget(row)
                if advanced_group is not None:
                    card_layout.addWidget(advanced_group)
                    self._set_group_content_visible(advanced_group, False)
                if card.isCheckable():
                    card.toggled.connect(lambda checked, group=card: self._set_group_content_visible(group, checked))
                    self._set_group_content_visible(card, False)
                cards.append(card)
            if page_id == "prompts":
                preview = QGroupBox("提示词预览")
                preview_layout = QVBoxLayout(preview)
                button = QPushButton("刷新预览")
                button.clicked.connect(self.refresh_prompt_preview)
                preview_layout.addWidget(button)
                self.prompt_preview_edit.setParent(preview)
                preview_layout.addWidget(self.prompt_preview_edit)
                cards.append(preview)
            if page_id == "everyday":
                layout_card = next(card for card in cards if card.property("settings_card_id") == "workspace_layout")
                self.reset_layout_btn.setParent(layout_card)
                layout_card.layout().addWidget(self.reset_layout_btn)
            from gui.models.settings_catalog import get_category
            category = get_category(page_id)
            pages[page_id] = self._compose_settings_page(category.title, category.description, cards)
            old.deleteLater()

    def _compose_settings_page(self, title: str, description: str, groups) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 19px; font-weight: 600;")
        layout.addWidget(heading)
        hint = QLabel(description)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)
        for group in groups:
            if group is None:
                continue
            group.setParent(content)
            content_layout.addWidget(group)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    @staticmethod
    def _remove_settings_group(page: QWidget, title: str):
        group = next((item for item in page.findChildren(QGroupBox) if item.title() == title), None)
        if group is not None:
            parent_layout = group.parentWidget().layout()
            if parent_layout is not None:
                parent_layout.removeWidget(group)
            group.setParent(None)
        return group

    def _take_settings_group(self, page: QWidget, title: str):
        return self._remove_settings_group(page, title)

    def _on_settings_nav_row_changed(self, row: int) -> None:
        page_index = self._nav_row_to_page.get(row)
        if page_index is not None:
            self.tabs.setCurrentIndex(page_index)

    def _sync_settings_nav(self, page_index: int) -> None:
        for row, target in self._nav_row_to_page.items():
            if target == page_index:
                self.settings_nav.blockSignals(True)
                self.settings_nav.setCurrentRow(row)
                self.settings_nav.blockSignals(False)
                return

    # —— 可搜索设置目录 ——

    def _build_search_bar(self) -> QWidget:
        box = QWidget()
        box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        outer = QVBoxLayout(box)
        outer.setSpacing(8)
        outer.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        self.settings_search_input = QLineEdit()
        self.settings_search_input.setPlaceholderText("搜索设置，例如「主题」「API Key」「重命名」…")
        self.settings_search_input.setClearButtonEnabled(True)
        self.settings_search_input.textChanged.connect(self._on_settings_search)
        row.addWidget(self.settings_search_input, 1)
        outer.addLayout(row)

        self.settings_search_model = QStandardItemModel(self)
        self.settings_completer = QCompleter(self.settings_search_model, self)
        self.settings_completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.settings_completer.setMaxVisibleItems(8)
        self.settings_search_results = QListView()
        self.settings_search_results.setObjectName("settings_search_results")
        self.settings_search_results.setTextElideMode(Qt.ElideRight)
        self.settings_completer.setPopup(self.settings_search_results)
        self.settings_completer.setCompletionRole(Qt.UserRole + 1)
        self.settings_search_input.setCompleter(self.settings_completer)
        self.settings_completer.activated[QModelIndex].connect(self._on_search_result_activated)
        return box

    def _on_settings_search(self, text: str) -> None:
        query = (text or "").strip()
        self.settings_search_model.clear()
        if not query:
            self.settings_search_results.hide()
            return
        matches = search_entries(query, getattr(self, "_ui_search_entries", None))
        if not matches:
            item = QStandardItem("未找到匹配设置")
            item.setFlags(Qt.NoItemFlags)
            self.settings_search_model.appendRow(item)
        else:
            for match in matches:
                tab_index = getattr(self, "_search_tab_by_key", {}).get(
                    match.entry.widget_key, match.entry.tab_index
                )
                label = f"[{self.tabs.tabText(tab_index)}] {match.entry.label}"
                item = QStandardItem(label)
                item.setToolTip(match.entry.effect or label)
                item.setData(match.entry.widget_key, Qt.UserRole)
                item.setData(query, Qt.UserRole + 1)
                self.settings_search_model.appendRow(item)
        self.settings_completer.setCompletionPrefix("")
        if self.settings_search_input.isVisible() and self.settings_search_input.hasFocus():
            self.settings_completer.complete()

    def _on_search_result_activated(self, item) -> None:
        widget_key = item.data(Qt.UserRole)
        if widget_key:
            self.settings_search_results.hide()
            self.focus_setting(widget_key)

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

        target_widget = self._as_widget(target)
        if target_widget is None:
            return

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
        if getattr(widget, "_settings_autosave_bound", False) or widget.property("settings_view_only"):
            return
        bound = False
        # ModelPicker 等复合控件通过自定义 textChanged 暴露值变化；先绑定
        # 自定义信号，再由其内部控件的标准信号负责普通文本编辑。
        custom_signal = getattr(widget, "textChanged", None)
        if custom_signal is not None and not isinstance(widget, (QLineEdit, QPlainTextEdit)):
            try:
                custom_signal.connect(self._on_pref_changed)
                custom_signal.connect(self._mark_manual_dirty)
                bound = True
            except (AttributeError, TypeError):
                pass
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(self._on_pref_changed)
            bound = True
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
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
        tab = getattr(self, "_search_tab_by_key", {}).get(widget_key)
        if tab is not None:
            self.tabs.setCurrentIndex(tab)
        elif entry is not None:
            self.tabs.setCurrentIndex(entry.tab_index)
        for i in range(self.http400_settings.stack.count()):
            editor = self.http400_settings.stack.widget(i)
            if widget is editor or editor.isAncestorOf(widget):
                self.http400_settings.scope.setCurrentIndex(i)
                if widget is editor.advanced_body or editor.advanced_body.isAncestorOf(widget):
                    editor.advanced_toggle.setChecked(True)
                break
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QGroupBox) and parent.isCheckable():
                parent.setChecked(True)
            if parent.layout() is not None:
                parent.layout().activate()
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget)
                if widget_key == "http400_recovery":
                    # 显示区域标题与整段表单，避免搜索只把范围选择框露在页面底部。
                    top = widget.mapTo(parent.widget(), widget.rect().topLeft()).y()
                    parent.verticalScrollBar().setValue(max(0, top - 80))
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
        self._pending_search_widget = widget
        self._search_scroll_timer.start(20)
        return True

    def _finish_search_focus(self):
        widget = getattr(self, "_pending_search_widget", None)
        if widget is None:
            return
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QGroupBox) and parent.isCheckable():
                parent.setChecked(True)
            if parent.layout() is not None:
                parent.layout().activate()
            if isinstance(parent, QScrollArea):
                parent.widget().layout().activate()
                top = widget.mapTo(parent.widget(), widget.rect().topLeft()).y()
                parent.verticalScrollBar().setValue(max(0, top - 60))
                break
            parent = parent.parentWidget()

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
        if not self._suppress_provider_signals:
            self._schedule_settings_save()

    def _schedule_settings_save(self) -> None:
        if self._suppress_provider_signals:
            return
        if hasattr(self, "settings_nav"):
            row = self.settings_nav.currentRow()
            page = self._nav_row_to_page.get(row)
            if page is not None:
                self.settings_nav.item(row).setText("   " + self.tabs.tabText(page) + " · 待保存")
        self.prefs_controller.schedule_save(
            self._collect_all_settings,
            validator=self._commit_all_settings,
            after_save=self._save_secondary_settings,
        )

    def _mark_manual_dirty(self, *_args) -> None:
        if not self._suppress_provider_signals:
            self._manual_dirty = True

    def _collect_all_settings(self):
        """只收集与校验，不在输入事件中写盘。"""
        return self.apply_settings(silent=True, persist=False)

    def _commit_all_settings(self):
        """校验设置；不同配置文件独立提交。"""
        import re
        config = SettingsManager.get_setting(self.settings, "processing.media_watch", {}) or {}
        if config.get("regex_find"):
            re.compile(config["regex_find"])
        return True

    def _save_secondary_settings(self):
        """所有存储目标成功后应用连接与监控改动；失败时允许重试。"""
        try:
            current_ai = self._ai_configuration()
            if current_ai != self._applied_ai_configuration and hasattr(self.service, "reload_ai_from_settings"):
                self.service.reload_ai_from_settings()
            self._applied_ai_configuration = current_ai
            self._refresh_provider_labels()
            from core.media_watch import set_windows_startup, should_start_with_windows
            watch = SettingsManager.get_setting(self.settings, "processing.media_watch", {}) or {}
            if watch != self._applied_watch_configuration:
                import copy
                if should_start_with_windows(watch) != should_start_with_windows(self._applied_watch_configuration):
                    set_windows_startup(should_start_with_windows(watch))
                self.media_watch_changed.emit()
                self._applied_watch_configuration = copy.deepcopy(watch)
            self._refresh_effective_config_summary()
            return True
        except Exception:  # noqa: BLE001 - 避免在状态文案中泄露凭据或原始连接数据
            return False

    def _on_target_state(self, target, state):
        self._target_states[target] = state
        names = {"settings": "设置文件", "tag_config": "提示词与导出配置"}
        states = {"pending": "待保存", "saved": "已保存", "failed": "保存失败"}
        if hasattr(self, "target_status_label"):
            self.target_status_label.setText(" · ".join(
                names[key] + "：" + states.get(self._target_states.get(key), "已保存")
                for key in names))

    def _on_preferences_saved(self, message: str) -> None:
        self._manual_dirty = False
        for row, page in self._nav_row_to_page.items():
            self.settings_nav.item(row).setText("   " + self.tabs.tabText(page))
        self._set_save_status(message)
        if hasattr(self, "save_btn"):
            self.save_btn.setVisible(False)
        self.ui_preferences_applied.emit()

    def _set_save_status(self, text: str, error: bool = False) -> None:
        if not hasattr(self, "save_status_label"):
            return
        self.save_status_label.setText(text)
        color = "#f48771" if error else "#89d185"
        self.save_status_label.setStyleSheet(
            f"color: {color}; font-size: 12px;"
        )
        if hasattr(self, "save_btn"):
            self.save_btn.setVisible(bool(error))

    def _install_card_reset_buttons(self) -> None:
        """只给已声明恢复范围的设置卡片提供局部恢复入口。"""
        for page_index in range(self.tabs.count()):
            page = self.tabs.widget(page_index)
            for group in page.findChildren(QGroupBox):
                reset_group = str(group.property("settings_reset_group") or "")
                if not reset_group or group.property("settings_reset_bound"):
                    continue
                button = QPushButton("恢复本卡片默认")
                button.setObjectName("settings_group_reset")
                button.setToolTip("只恢复这张卡片负责的设置项；连接密钥、监控目录和素材不会改变。")
                button.clicked.connect(
                    lambda _checked=False, key=reset_group: self._reset_category_defaults(key)
                )
                group_layout = group.layout()
                if isinstance(group_layout, QFormLayout):
                    group_layout.addRow("", button)
                elif group_layout is not None:
                    group_layout.addWidget(button)
                if group.isCheckable():
                    button.setVisible(group.isChecked())
                group.setProperty("settings_reset_bound", True)

    def _reset_category_defaults(self, reset_group) -> None:
        if isinstance(reset_group, int):
            reset_group = {0: "everyday", 1: "video_analysis", 2: "model_assignment", 3: "prompts", 4: "rename_template"}.get(reset_group, "")
        reset_group = str(reset_group or "")
        if not reset_group:
            return
        reply = QMessageBox.question(
            self,
            "恢复本卡片默认",
            f"确定恢复“{self._reset_group_title(reset_group)}”中的设置吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._suppress_provider_signals = True
        try:
            self._apply_reset_group(reset_group)
        finally:
            self._suppress_provider_signals = False
        self._schedule_settings_save()
        self._set_save_status(f"已恢复“{self._reset_group_title(reset_group)}”，正在保存…")

    @staticmethod
    def _reset_group_title(reset_group: str) -> str:
        return {
            "everyday": "界面与工作区", "video_analysis": "视频分析", "ordinary_retries": "普通错误重试",
            "http400_recovery": "HTTP 400 恢复", "failure_defaults": "全部失败处理",
            "provider_model_defaults": "供应商默认模型", "task_model_routing": "任务模型路由",
            "model_assignment": "AI 模型分配", "analysis_prompt": "分析角色提示",
            "group_prompts": "标签组引导词", "tag_ai_prompts": "标签库 AI 模板",
            "prompts": "提示词与标签", "rename_template": "重命名模板",
            "xmp_export": "XMP 设置", "ale_export": "ALE 导出字段", "monitor_defaults": "监控全局默认",
        }.get(reset_group, reset_group)

    @staticmethod
    def _set_control_value(widget, value) -> None:
        if widget is None:
            return
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(int(value))
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:
                index = widget.findText(str(value))
            if index >= 0:
                widget.setCurrentIndex(index)
        elif isinstance(widget, QPlainTextEdit):
            widget.setPlainText(str(value or ""))
        elif isinstance(widget, QLineEdit):
            widget.setText(", ".join(value) if isinstance(value, list) else str(value or ""))
        elif isinstance(widget, ModelPicker):
            widget.setText(str(value or ""))

    def _apply_reset_group(self, reset_group: str) -> None:
        import copy
        from core.video_organizer_service import DEFAULT_SETTINGS
        from core.model_providers import update_provider, list_providers
        from core.watch_config import DEFAULTS as WATCH_DEFAULTS
        from core.watch_config import normalize_watch_config
        defaults = copy.deepcopy(DEFAULT_SETTINGS)
        card_groups = {"appearance", "material_display", "workspace_layout", "work_scope",
                       "analysis_pipeline", "analysis_preparation", "ingestion", "audio_transcription"}
        if reset_group in card_groups:
            from gui.models.settings_catalog import SETTINGS_ENTRIES
            for entry in SETTINGS_ENTRIES:
                if entry.reset_group != reset_group:
                    continue
                value = SettingsManager.get_setting(defaults, entry.key, entry.default_value)
                SettingsManager.update_setting(self.settings, entry.key, copy.deepcopy(value))
                if entry.widget_key == "thumbnail_size":
                    self.thumb_w_spin.setValue(value[0])
                    self.thumb_h_spin.setValue(value[1])
                else:
                    self._set_control_value(self._search_widget_map.get(entry.widget_key), value)
            return
        controls = {
            "everyday": {
                "font_spin": defaults["ui_preferences"]["font_size"],
                "thumb_w_spin": defaults["ui_preferences"]["thumbnail_size"][0],
                "thumb_h_spin": defaults["ui_preferences"]["thumbnail_size"][1],
                "theme_combo": defaults["ui_preferences"]["theme"],
                "default_view_combo": defaults["ui_preferences"]["default_view"],
                "sidebar_spin": defaults["ui_preferences"]["sidebar_width"],
                "detail_expanded_cb": defaults["ui_preferences"]["detail_panel_expanded"],
                "remember_scope_cb": defaults["ui_preferences"]["remember_work_scope"],
            },
            "video_analysis": {
                "workers_spin": defaults["processing"]["max_workers"],
                "extract_parallel_spin": defaults["processing"]["extract_parallel"],
                "extract_workers_spin": defaults["processing"]["extract_workers"],
                "frames_spin": defaults["processing"]["max_frames"],
                "quality_spin": defaults["processing"]["jpeg_quality"],
                "scene_detect_cb": defaults["processing"]["enable_scene_detection"],
                "audio_cb": defaults["processing"]["enable_audio_transcription"],
            },
            "ordinary_retries": {
                "call_extra_spin": defaults["processing"]["analysis_retry"]["call_extra_attempts"],
                "item_max_spin": defaults["processing"]["analysis_retry"]["item_max_attempts"],
                "batch_rerun_cb": defaults["processing"]["analysis_retry"]["batch_rerun_enabled"],
            },
            "rename_template": {"pattern_input": defaults["rename_pattern"]},
            "xmp_export": {
                "auto_xmp_cb": defaults["processing"]["auto_sync_xmp_after_analysis"],
                "xmp_lang_combo": "zh-CN", "xmp_hierarchical_cb": True, "xmp_prefix_cat_cb": True,
            },
            "ale_export": {"ale_columns_edit": ["Name", "Keywords", "Category", "Summary"]},
        }
        if reset_group == "failure_defaults":
            self._apply_reset_group("ordinary_retries")
            self._apply_reset_group("http400_recovery")
            return
        if reset_group in controls:
            config = self.settings
            for name, value in controls[reset_group].items():
                widget = getattr(self, name, None)
                self._set_control_value(widget, value)
            if reset_group == "everyday":
                pref = config.setdefault("ui_preferences", {})
                for key, value in defaults["ui_preferences"].items():
                    if key != "last_work_scope":
                        pref[key] = copy.deepcopy(value)
            elif reset_group == "video_analysis":
                for key in ("max_workers", "extract_parallel", "extract_workers", "max_frames",
                            "jpeg_quality", "enable_scene_detection", "enable_audio_transcription"):
                    config.setdefault("processing", {})[key] = copy.deepcopy(defaults["processing"][key])
            elif reset_group == "ordinary_retries":
                config.setdefault("processing", {}).setdefault("analysis_retry", {}).update(copy.deepcopy(defaults["processing"]["analysis_retry"]))
            elif reset_group == "rename_template":
                config["rename_pattern"] = defaults["rename_pattern"]
            elif reset_group == "xmp_export":
                config.setdefault("processing", {})["auto_sync_xmp_after_analysis"] = defaults["processing"]["auto_sync_xmp_after_analysis"]
                self.service.tag_config.setdefault("global_settings", {}).update({"language": "zh-CN"})
                export = self.service.tag_config.setdefault("global_settings", {}).setdefault("export_schemes", {})
                export.update({"xmp_hierarchical": True, "xmp_prefix_category": True})
            elif reset_group == "ale_export":
                self.service.tag_config.setdefault("global_settings", {}).setdefault("export_schemes", {})["ale_columns"] = ["Name", "Keywords", "Category", "Summary"]
            return
        if reset_group in {"provider_model_defaults", "task_model_routing", "model_assignment"}:
            if reset_group in {"provider_model_defaults", "model_assignment"}:
                model_defaults = defaults["api"]["model_personalization"]
                for provider in list_providers(self.settings):
                    if reset_group == "model_assignment" or provider["id"] == self._selected_provider_id():
                        update_provider(self.settings, provider["id"], models=model_defaults)
                for widget, value in ((self.cls_model_input, model_defaults["video_classification"]), (self.tag_model_input, model_defaults["tag_generation"]), (self.desc_model_input, model_defaults["content_description"])):
                    self._set_control_value(widget, value)
            if reset_group in {"task_model_routing", "model_assignment"}:
                replace_task_model_routing(self.settings, {key: {"provider_id": "", "model": ""} for key in TASK_ROUTE_KEYS})
                self._load_task_routes_ui()
            self._refresh_provider_list(select_id=self._selected_provider_id())
            return
        if reset_group in {"http400_recovery", "failure_defaults"}:
            processing = self.settings.setdefault("processing", {})
            processing.pop("http400_recovery", None)
            self.http400_settings.global_editor.load_override({})
            self.http400_settings._sync_parent()
            for editor in (self.http400_settings.task_editor, self.http400_settings.audio_editor):
                editor.load_override({})
            return
        if reset_group in {"analysis_prompt", "group_prompts", "tag_ai_prompts", "prompts"}:
            if reset_group in {"analysis_prompt", "prompts"}:
                default_prompt = "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。"
                self.service.tag_config.setdefault("global_settings", {})["system_prompt"] = default_prompt
                self.system_prompt_edit.setPlainText(default_prompt)
            if reset_group in {"group_prompts", "prompts"}:
                for group in self.service.tag_config.get("tag_groups", []):
                    group.setdefault("rules", {})["local_prompt"] = ""
                for edit in self._local_prompt_edits.values():
                    edit.setPlainText("")
            if reset_group in {"tag_ai_prompts", "prompts"}:
                from core.tag_ai_assist import DEFAULT_TAG_AI_PROMPTS
                self.settings["tag_ai_prompts"] = copy.deepcopy(DEFAULT_TAG_AI_PROMPTS)
                for key, edit in self._tag_ai_prompt_edits.items():
                    edit.setPlainText(DEFAULT_TAG_AI_PROMPTS.get(key, ""))
            return
        if reset_group == "monitor_defaults":
            config = normalize_watch_config(self.settings.get("processing", {}).get("media_watch", {}))
            for key, value in WATCH_DEFAULTS.items():
                config[key] = copy.deepcopy(value)
            self.settings.setdefault("processing", {})["media_watch"] = config
            self.media_watch_page.refresh()

    def _reset_prompt_defaults(self) -> None:
        """只恢复提示词内容，保留用户的标签组、分类和标签数据。"""
        import copy
        from core.tag_ai_assist import DEFAULT_TAG_AI_PROMPTS

        tag_config = self.service.tag_config or {}
        tag_config.setdefault("global_settings", {})[
            "system_prompt"
        ] = "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。"
        for group in tag_config.get("tag_groups") or []:
            if isinstance(group, dict):
                group.setdefault("rules", {})["local_prompt"] = ""
        self.settings["tag_ai_prompts"] = copy.deepcopy(DEFAULT_TAG_AI_PROMPTS)
        self.service.tag_config = tag_config

    def _on_rename_changed(self) -> None:
        """重命名模板只在命名与导出中编辑。"""
        SettingsManager.update_setting(
            self.settings, "rename_pattern", self.pattern_input.text().strip()
        )
        self._on_pref_changed()

    def eventFilter(self, obj, event):
        if event.type() == event.Type.FocusOut and isinstance(obj, (QLineEdit, QPlainTextEdit)):
            if not self._suppress_provider_signals and not getattr(obj, "isReadOnly", lambda: False)():
                self.prefs_controller.flush_now()
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
                self._highlight_catalog_entry(entry.widget_key)
        return super().eventFilter(obj, event)

    def _highlight_catalog_entry(self, widget_key: str) -> None:
        results = getattr(self, "settings_search_results", None)
        if results is None or not results.isVisible():
            return
        for row in range(results.model().rowCount()):
            item = results.model().index(row, 0)
            if item.data(Qt.UserRole) == widget_key:
                results.setCurrentIndex(item)
                break

    def _data_directory_text(self) -> str:
        try:
            inspection = self.service.data_directory_inspection()
            return (
                f"当前目录：{inspection.active_root}\n"
                f"便携默认目录：{inspection.default_root}\n"
                f"状态：{inspection.message}"
            )
        except Exception as exc:
            return f"当前目录不可用：{exc}"

    def _has_running_background_work(self) -> bool:
        return _window_has_active_background_work(self.window())

    def _finish_data_directory_switch(self, target: str) -> None:
        self.data_directory_label.setText(
            f"已选择：{target}\n请重启应用后使用新目录。"
        )
        QMessageBox.information(
            self,
            "目录已准备",
            "目录已验证并记录。应用将退出，请重新启动以使用新目录。",
        )
        application = QApplication.instance()
        if application is None:
            return
        application.quit()

    def _run_existing_directory_open(
        self,
        manager,
        target: str,
        progress: QProgressDialog,
    ) -> None:
        def on_ok(_result):
            progress.close()
            self._finish_data_directory_switch(target)

        def on_fail(message: str):
            progress.close()
            QMessageBox.critical(self, "打开数据目录失败", str(message))

        run_io_job(
            self,
            fn=lambda: manager.open_existing(target),
            on_ok=on_ok,
            on_fail=on_fail,
            on_start=progress.show,
            busy_message="",
        )

    def _choose_data_directory(self) -> None:
        if not self._data_directory_switch_enabled:
            return
        target = QFileDialog.getExistingDirectory(self, "选择管理数据目录")
        if not target:
            return
        if self._has_running_background_work():
            QMessageBox.warning(
                self,
                "无法切换数据目录",
                "当前仍有后台任务运行。请等待任务结束后再切换数据目录。",
            )
            return
        if not self.prefs_controller.flush():
            QMessageBox.warning(
                self,
                "无法切换数据目录",
                "尚未保存的界面偏好保存失败。请先解决保存错误后再切换。",
            )
            return
        try:
            from core.data_directory import DataDirectoryManager

            manager = DataDirectoryManager()
            if manager.has_management_data(target):
                answer = QMessageBox.question(
                    self,
                    "打开已有数据目录",
                    "目标目录已有管理数据。是否打开该目录？\n不会与当前目录合并。重启应用后生效。",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                progress = QProgressDialog(
                    "正在校验并打开已有管理数据目录…",
                    "",
                    0,
                    0,
                    self.window(),
                )
                progress.setWindowTitle("打开管理数据目录")
                progress.setWindowModality(Qt.WindowModal)
                progress.setCancelButton(None)
                progress.setMinimumDuration(0)
                self._data_directory_progress = progress
                self._run_existing_directory_open(manager, target, progress)
                return

            answer = QMessageBox.question(
                self,
                "确认搬迁管理数据",
                "将当前管理数据备份并复制到目标目录，验证成功后切换；原目录会保留。\n"
                "原始视频不会移动。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

            progress = QProgressDialog(
                "正在备份、复制并验证管理数据…",
                "",
                0,
                0,
                self.window(),
            )
            progress.setWindowTitle("搬迁管理数据")
            progress.setWindowModality(Qt.WindowModal)
            progress.setCancelButton(None)
            progress.setMinimumDuration(0)
            self._data_directory_progress = progress

            def on_ok(_result):
                progress.close()
                self._finish_data_directory_switch(target)

            def on_fail(message: str):
                progress.close()
                QMessageBox.critical(self, "数据目录搬迁失败", str(message))

            run_io_job(
                self,
                fn=lambda: self.service.migrate_data_directory(target),
                on_ok=on_ok,
                on_fail=on_fail,
                on_start=progress.show,
                busy_message="",
            )
        except DataDirectoryError as exc:
            QMessageBox.critical(self, "数据目录操作失败", str(exc))

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

        # 管理数据目录：路径选择只改变下次启动的活动目录，不移动原始视频。
        data_group = QGroupBox("管理数据目录")
        data_layout = QVBoxLayout(data_group)
        data_row = QHBoxLayout()
        self.data_directory_label = QLabel()
        self.data_directory_label.setWordWrap(True)
        self.data_directory_label.setText(self._data_directory_text())
        data_row.addWidget(self.data_directory_label, 1)
        self.data_directory_btn = QPushButton("选择或搬迁目录")
        self.data_directory_btn.clicked.connect(self._choose_data_directory)
        self.data_directory_btn.setEnabled(self._data_directory_switch_enabled)
        if not self._data_directory_switch_enabled:
            self.data_directory_btn.setToolTip(
                "临时人工测试会话中禁用目录切换，以保护应用真实数据定位记录。"
            )
        data_row.addWidget(self.data_directory_btn)
        data_layout.addLayout(data_row)
        data_hint = QLabel(
            "管理数据包括数据库、设置、标签配置、备份和缩略图；原始视频路径不会因目录搬迁改变。"
            "目标已有数据时只能显式打开，不会自动合并。"
        )
        data_hint.setWordWrap(True)
        data_hint.setStyleSheet("color: #888; font-size: 12px;")
        if not self._data_directory_switch_enabled:
            data_hint.setText(
                data_hint.text()
                + " 临时人工测试会话中已禁用目录切换，以免触碰应用真实定位记录。"
            )
        data_layout.addWidget(data_hint)
        form.addRow(data_group)

        # 处理配置
        proc_group = QGroupBox("视频分析工作流")
        proc_group.setProperty("settings_reset_group", "video_analysis")
        proc_form = QFormLayout(proc_group)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 512)
        self.workers_spin.setValue(
            int(SettingsManager.get_setting(self.settings, "processing.max_workers", 16) or 16)
        )
        self.workers_spin.setToolTip(
            "同时处理多少条视频（包括抽帧、等待AI和整理结果），上限512。不是每秒请求数；图片准备好后才请求AI。建议先用8～16。"
        )

        self.extract_parallel_spin = QSpinBox()
        self.extract_parallel_spin.setRange(1, 512)
        self.extract_parallel_spin.setValue(
            int(
                SettingsManager.get_setting(
                    self.settings, "processing.extract_parallel", 4
                )
                or 4
            )
        )
        self.extract_parallel_spin.setToolTip(
            "视频分析前，同时为多少条视频解码并准备AI图片，上限512。默认4；4K建议2～4。若AI请求稀疏且CPU/磁盘有余量，可逐步调高；不要超过分析任务并行数。"
        )

        self.extract_workers_spin = QSpinBox()
        self.extract_workers_spin.setRange(1, 512)
        self.extract_workers_spin.setValue(
            int(
                SettingsManager.get_setting(
                    self.settings, "processing.extract_workers", 8
                )
                or 8
            )
        )
        self.extract_workers_spin.setToolTip(
            "仅在新视频入库时生成列表缩略图，上限512，建议4～8。不会提高分析AI请求并发，也不控制标签库AI分类。"
        )

        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 50)
        self.frames_spin.setValue(SettingsManager.get_setting(self.settings, "processing.max_frames", 10))

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(10, 100)
        self.quality_spin.setValue(SettingsManager.get_setting(self.settings, "processing.jpeg_quality", 80))

        self.scene_detect_cb = QCheckBox("启用智能场景检测")
        self.scene_detect_cb.setToolTip(
            "开启时会先遍历解码视频寻找场景，长视频的图片准备可能较慢；"
            "关闭后按时间均匀抽帧，速度通常更快，但送给AI的画面会不同。不会自动替你关闭。"
        )
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

        proc_form.addRow("同时分析的视频数:", self.workers_spin)
        proc_form.addRow("分析图片准备并行数:", self.extract_parallel_spin)
        proc_form.addRow("入库缩略图并行数:", self.extract_workers_spin)
        concurrency_help = QLabel(
            "分析流程：准备图片 → 请求 AI → 保存结果。\n"
            "“同时分析的视频数”是整条流程的任务上限，32不代表每秒32次请求；"
            "“分析图片准备并行数”决定前置抽帧速度。\n"
            "智能场景检测会先遍历解码视频，可能延后AI请求；关闭则均匀抽帧，画面选择会不同。\n"
            "“入库缩略图并行数”只用于新素材入库；以上三项不控制标签库 AI 分类。保存后用于下一次任务。"
        )
        concurrency_help.setWordWrap(True)
        proc_form.addRow(concurrency_help)
        proc_form.addRow("AI 分析抽帧数:", self.frames_spin)
        proc_form.addRow("JPEG 压缩质量:", self.quality_spin)
        proc_form.addRow(self.scene_detect_cb)
        proc_form.addRow(self.audio_cb)
        form.addRow(proc_group)
        self.processing_group = proc_group
        self.data_directory_group = data_group
        self.register_setting_widget("data_directory", self.data_directory_btn)

        # 分析重试（高级，ADR-0005）
        failure_group = QGroupBox("分析失败处理")

        failure_layout = QVBoxLayout(failure_group)
        retry_group = QGroupBox("普通错误重试（超时、限流等）")
        retry_group.setProperty("settings_reset_group", "ordinary_retries")
        retry_form = QFormLayout(retry_group)
        retry_hint = QLabel(
            "适用于超时、限流等可重试错误。HTTP 400 使用下方独立规则，不重复计次。重试会增加 API 请求与费用。"
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
        retry_form.addRow("单次请求额外重试:", self.call_extra_spin)
        retry_form.addRow("单视频流程最多尝试:", self.item_max_spin)
        retry_form.addRow(self.batch_rerun_cb)
        failure_layout.addWidget(retry_group)
        from gui.widgets.recovery_controls import RecoverySettingsPanel
        recovery_group = QGroupBox("HTTP 400 重试与备用 API")
        recovery_group.setProperty("settings_reset_group", "http400_recovery")
        recovery_layout = QVBoxLayout(recovery_group)
        self.http400_settings = RecoverySettingsPanel(self.service, provider_resolver=self._model_list_provider)
        self.http400_settings.changed.connect(self._on_recovery_settings_changed)
        recovery_layout.addWidget(self.http400_settings)
        failure_layout.addWidget(recovery_group)
        self.register_setting_widget("http400_recovery", self.http400_settings.scope)
        form.addRow(failure_group)
        self.failure_group = failure_group

        # 重命名模板在命名与导出页面作为唯一编辑入口。
        rename_group = QGroupBox("自动重命名模板")
        rename_group.setProperty("settings_reset_group", "rename_template")
        rename_form = QFormLayout(rename_group)

        self.pattern_input = QLineEdit(SettingsManager.get_setting(self.settings, "rename_pattern", ""))
        self.pattern_input.setPlaceholderText("{category}-{tags}-{summary}-{original_name}")
        self.pattern_input.textChanged.connect(self._on_rename_changed)

        rename_form.addRow("命名模式:", self.pattern_input)
        rename_form.addRow(QLabel("可用变量: {category}, {tags}, {summary}, {original_name}"))
        form.addRow(rename_group)
        self.rename_group = rename_group

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
        QVBoxLayout(tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)

        ensure_providers(self.settings)

        # 敏感配置：自动保存并显示当前生效摘要
        hint = QLabel(
            "敏感配置会在停止输入约 800ms 或离开输入框后自动保存并重建 AI 客户端。"
            "供应商档案只保存显示名、API Key、Base URL（最多 5 个）。"
            "「当前供应商」是未单独配置任务时的默认回退。"
            "下方「任务模型路由」可为每个 AI 功能跨供应商选模型。"
            "进行中的分析本轮不中断。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        connection_title = QLabel("AI 连接")
        connection_title.setStyleSheet("font-size: 19px; font-weight: 600;")
        layout.addWidget(connection_title)
        hint.setText("连接只管理 API 地址与密钥，最多 5 个。默认回退连接用于未单独指定连接的任务。"
                     "模型请到「AI 模型分配」选择。停止输入 800ms 或失焦后自动保存，对后续请求生效。")
        layout.addWidget(hint)

        # —— 当前生效配置摘要（只读，不显示 Key）——
        effective_group = QGroupBox("当前生效配置")
        effective_layout = QVBoxLayout(effective_group)
        self.effective_config_label = QLabel()
        self.effective_config_label.setWordWrap(True)
        self.effective_config_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.effective_config_label.setStyleSheet("color: #89d185; font-size: 12px;")
        effective_layout.addWidget(self.effective_config_label)
        test_row = QHBoxLayout()
        self.connection_test_task_combo = QComboBox()
        for key in TASK_ROUTE_KEYS:
            self.connection_test_task_combo.addItem(TASK_ROUTE_LABELS.get(key, key), key)
        self.connection_test_btn = QPushButton("验证并刷新模型")
        self.connection_test_btn.clicked.connect(self._test_selected_connection)
        self.connection_test_status_label = QLabel("")
        self.connection_test_status_label.setStyleSheet("color: #888; font-size: 12px;")
        self.connection_test_task_label = QLabel("测试任务:")
        self.connection_test_task_label.setVisible(False)
        self.connection_test_task_combo.setVisible(False)
        test_row.addWidget(self.connection_test_task_label)
        test_row.addWidget(self.connection_test_task_combo, 1)
        test_row.addWidget(self.connection_test_btn)
        test_row.addWidget(self.connection_test_status_label, 2)
        test_group = QGroupBox("连接测试")
        test_layout = QVBoxLayout(test_group)
        test_layout.addLayout(test_row)
        layout.addWidget(test_group)

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
        self.set_current_btn = QPushButton("设为默认回退")
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

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("快速新增预设:"))
        self.provider_preset_combo = QComboBox()
        self.provider_preset_combo.addItem("自定义 OpenAI 兼容接口", "custom")
        for preset_id, preset in self._provider_presets().items():
            self.provider_preset_combo.addItem(preset["label"], preset_id)
        preset_row.addWidget(self.provider_preset_combo, 1)
        preset_hint = QLabel("先选预设，再点击“新增”")
        preset_hint.setStyleSheet("color: #888; font-size: 12px;")
        preset_row.addWidget(preset_hint)
        list_layout.addLayout(preset_row)

        self.register_setting_widget("default_connection", self.set_current_btn)
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
        self.api_key_input.setProperty("settings_flush_on_blur", True)
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.api_key_input, 1)
        self.api_key_visibility_btn = QPushButton("显示")
        self.api_key_visibility_btn.setCheckable(True)
        self.api_key_visibility_btn.toggled.connect(self._toggle_api_key_visibility)
        key_layout.addWidget(self.api_key_visibility_btn)
        self.api_url_input = QLineEdit()
        self.api_url_input.setProperty("settings_flush_on_blur", True)

        form.addRow("显示名:", self.provider_name_input)
        form.addRow("API Key:", key_row)
        form.addRow("API Base URL:", self.api_url_input)
        layout.addWidget(form_group)

        model_group = QGroupBox("连接默认模型")
        model_form = QFormLayout(model_group)
        self.model_provider_combo = QComboBox()
        self.model_provider_combo.currentIndexChanged.connect(self._select_model_provider)
        model_form.addRow("编辑此连接的默认模型:", self.model_provider_combo)

        self.cls_model_input = ModelPicker(self._model_list_provider)
        self.tag_model_input = ModelPicker(self._model_list_provider)
        self.desc_model_input = ModelPicker(self._model_list_provider)
        model_hint = QLabel("获取模型后，可输入关键词搜索并选择；也可手动填写自定义模型。选择后会自动保存。列表不代表模型支持图像或音频，请按任务用途选择。")
        model_hint.setWordWrap(True)
        model_form.addRow(model_hint)

        model_form.addRow("分类默认:", self.cls_model_input)
        model_form.addRow("标签默认:", self.tag_model_input)
        model_form.addRow("描述默认:", self.desc_model_input)
        model_group.setProperty("settings_reset_group", "provider_model_defaults")

        # —— 任务模型路由六槽 ——
        route_group = QGroupBox("任务模型路由（跨供应商）")
        route_group.setProperty("settings_reset_group", "task_model_routing")
        route_layout = QVBoxLayout(route_group)
        route_hint = QLabel(
            "每个 AI 功能可指定供应商 + 模型名。供应商选「（默认回退）」则使用当前供应商。"
        )
        route_hint.setWordWrap(True)
        route_hint.setStyleSheet("color: #888; font-size: 12px;")
        route_layout.addWidget(route_hint)

        self._route_widgets = {}
        common_group = QGroupBox("常用任务")
        common_layout = QVBoxLayout(common_group)
        tag_group = QGroupBox("标签库任务")
        tag_group.setCheckable(True)
        tag_group.setChecked(False)
        tag_layout = QVBoxLayout(tag_group)
        diagnosis_group = QGroupBox("诊断任务")
        diagnosis_group.setCheckable(True)
        diagnosis_group.setChecked(False)
        diagnosis_layout = QVBoxLayout(diagnosis_group)
        route_layout.addWidget(common_group)
        route_layout.addWidget(tag_group)
        route_layout.addWidget(diagnosis_group)
        tag_group.toggled.connect(lambda checked: self._set_group_content_visible(tag_group, checked))
        diagnosis_group.toggled.connect(lambda checked: self._set_group_content_visible(diagnosis_group, checked))
        self._set_group_content_visible(tag_group, False)
        self._set_group_content_visible(diagnosis_group, False)
        for key in TASK_ROUTE_KEYS:
            task_card = QGroupBox(TASK_ROUTE_LABELS.get(key, key))
            row = QVBoxLayout(task_card)
            label = QLabel(TASK_ROUTE_LABELS.get(key, key))
            label.setMinimumWidth(100)
            row.addWidget(label)
            prov_combo = QComboBox()
            prov_combo.setMinimumWidth(140)
            model_edit = ModelPicker(lambda combo=prov_combo: self._model_list_provider(combo.currentData() or self.settings.get("current_provider_id")), compact=True)
            model_edit.setPlaceholderText("沿用连接默认模型，或输入自定义模型")
            row.addWidget(prov_combo, 1)
            row.addWidget(model_edit, 1)
            target_layout = common_layout
            if key in {"pending_tag_ai", "synonym_audit", "standard_tag_ai"}:
                target_layout = tag_layout
            elif key == "error_diagnosis":
                target_layout = diagnosis_layout
            target_layout.addWidget(task_card)
            prov_combo.currentIndexChanged.connect(self._on_route_widget_changed)
            prov_combo.currentIndexChanged.connect(model_edit.refresh_connection)
            model_edit.textChanged.connect(self._on_route_widget_changed)
            effective = QLabel()
            effective.setWordWrap(True)
            row.addWidget(effective)
            self._route_widgets[key] = {"provider": prov_combo, "model": model_edit, "effective": effective}

        self._set_group_content_visible(tag_group, False)
        self._set_group_content_visible(diagnosis_group, False)

        # 连接管理与模型分配使用两个独立页面，共享原有控件和保存逻辑。
        model_container = QWidget()
        model_layout = QVBoxLayout(model_container)
        model_title = QLabel("AI 模型分配")
        model_title.setStyleSheet("font-size: 19px; font-weight: 600;")
        model_layout.addWidget(model_title)
        model_layout.addWidget(effective_group)
        model_group.setProperty("settings_reset_group", "provider_model_defaults")
        model_layout.addWidget(route_group)
        model_layout.addWidget(model_group)
        model_layout.addStretch()
        model_scroll = QScrollArea()
        model_scroll.setWidgetResizable(True)
        model_scroll.setFrameShape(QFrame.NoFrame)
        model_scroll.setWidget(model_container)
        scroll.setWidget(container)

        self._provider_ui_ready = True
        self._refresh_provider_list(select_id=self.settings.get("current_provider_id"))
        self._load_task_routes_ui()
        self._refresh_effective_config_summary()
        self.api_key_input.textChanged.connect(self._refresh_model_pickers)
        self.api_url_input.textChanged.connect(self._refresh_model_pickers)
        self.register_setting_widget("api_key", self.api_key_input)
        self.register_setting_widget("api_url", self.api_url_input)
        self.register_setting_widget("provider_list", self.provider_list)
        self.register_setting_widget("provider_name", self.provider_name_input)
        self.register_setting_widget("provider_default_classification", self.cls_model_input)
        self.register_setting_widget("provider_default_tags", self.tag_model_input)
        self.register_setting_widget("provider_default_description", self.desc_model_input)
        self.ai_connection_page = scroll
        self.ai_model_page = model_scroll
        return scroll, model_scroll

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.api_key_input.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.api_key_visibility_btn.setText("隐藏" if visible else "显示")

    def _schedule_ai_settings_save(self) -> None:
        self._schedule_settings_save()

    def _model_list_provider(self, provider_id=None):
        """读取表单当前连接，支持未保存的Key/地址且不写入设置。"""
        pid = provider_id if provider_id is not None else self._selected_provider_id()
        provider = next((dict(p) for p in list_providers(self.settings) if p["id"] == pid), None)
        if provider is None:
            raise ValueError("请先添加并选择供应商。")
        if self._provider_ui_ready and pid == self._selected_provider_id():
            provider["api_key"] = self.api_key_input.text().strip()
            provider["base_url"] = self.api_url_input.text().strip()
        return provider

    @staticmethod
    def _provider_presets():
        """常见连接预设只负责填表，不改变后端路由协议。"""
        return {
            "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1"},
            "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com/v1"},
            "google": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
            "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
            "ollama": {"label": "Ollama", "base_url": "http://localhost:11434/v1"},
        }

    @staticmethod
    def _set_group_content_visible(group, visible: bool) -> None:
        layout = group.layout()
        if layout is None:
            return
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setVisible(bool(visible))
            child_layout = item.layout() if item is not None else None
            if child_layout is not None:
                for child_index in range(child_layout.count()):
                    child = child_layout.itemAt(child_index)
                    child_widget = child.widget() if child is not None else None
                    if child_widget is not None:
                        child_widget.setVisible(bool(visible))

    def _refresh_model_pickers(self, *_):
        if self._suppress_provider_signals:
            return
        for picker in self.findChildren(ModelPicker):
            picker.refresh_connection()

    def _refresh_effective_config_summary(self) -> None:
        """显示当前实际生效的路由和副作用开关，不显示凭据。"""
        if not hasattr(self, "effective_config_label"):
            return
        try:
            current = resolve_current_provider(self.settings)
            current_name = current.get("display_name") or current.get("id") or "未命名"
            key_state = "已配置" if str(current.get("api_key") or "").strip() else "未配置"
            url_state = "已配置" if str(current.get("base_url") or "").strip() else "未配置"
            lines = [
                f"默认回退连接：{current_name}（API Key {key_state}，Base URL {url_state}）",
                "任务模型路由：各任务下方显示实际连接、模型及继承来源。",
            ]
            for key in TASK_ROUTE_KEYS:
                route = resolve_task_route(self.settings, key)
                provider = route.get("display_name") or route.get("provider_id") or "未命名"
                model = route.get("model") or "未设置"
                slot = get_task_model_routing(self.settings).get(key) or {}
                source = "指定连接" if slot.get("provider_id") else "默认回退连接"
                source += " · 指定模型" if slot.get("model") else " · 继承连接默认模型"
                widgets = getattr(self, "_route_widgets", {}).get(key)
                if widgets:
                    widgets["effective"].setText(f"实际使用：{provider} / {model}（{source}）；后续 AI 请求生效")
            self.effective_config_label.setText("\n".join(lines))
        except Exception:
            self.effective_config_label.setText("当前生效配置暂时无法读取，请检查连接和模型分配。")

    def _test_selected_connection(self) -> None:
        """验证当前选中连接并刷新该连接的模型候选，不切换默认路由。"""
        self._flush_provider_form_to_settings()
        provider = self._model_list_provider()
        self.connection_test_btn.setEnabled(False)
        self.connection_test_status_label.setText("正在验证连接并刷新模型…")

        def on_ok(models):
            self.connection_test_btn.setEnabled(True)
            from gui.services.model_catalog import model_catalog
            model_catalog().store(provider, models)
            count = len(models) if isinstance(models, list) else 0
            self.connection_test_status_label.setStyleSheet("color: #89d185; font-size: 12px;")
            self.connection_test_status_label.setText(f"连接成功，发现 {count} 个模型候选")
            self._refresh_model_pickers()
            self._refresh_effective_config_summary()

        def on_fail(message):
            self.connection_test_btn.setEnabled(True)
            self.connection_test_status_label.setStyleSheet("color: #f48771; font-size: 12px;")
            self.connection_test_status_label.setText("连接失败，请检查密钥、地址与网络后重试。")
            self._refresh_effective_config_summary()

        run_io_job(
            self,
            fn=lambda: self.service.ai.gateway.list_models(route_override=provider),
            on_ok=on_ok,
            on_fail=on_fail,
            busy_message="正在测试 AI 连接…",
        )

    def _on_route_widget_changed(self, *_args) -> None:
        if self._suppress_provider_signals or not getattr(self, "_provider_ui_ready", False):
            return
        self._flush_task_routes_to_settings()
        self._refresh_effective_config_summary()

    def _load_task_routes_ui(self) -> None:
        if not getattr(self, "_route_widgets", None):
            return
        ensure_providers(self.settings)
        providers = list_providers(self.settings)
        routing = get_task_model_routing(self.settings)
        was_suppressed = self._suppress_provider_signals
        self._suppress_provider_signals = True
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
        self._suppress_provider_signals = was_suppressed
        self._refresh_effective_config_summary()

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
        was_suppressed = self._suppress_provider_signals
        self._suppress_provider_signals = True
        try:
            self.provider_name_input.setText(provider.get("display_name") or "")
            self.api_key_visibility_btn.setChecked(False)
            self.api_key_input.setEchoMode(QLineEdit.Password)
            self.api_key_input.setText(provider.get("api_key") or "")
            self.api_url_input.setText(provider.get("base_url") or "")
            models = provider.get("models") or {}
            self.cls_model_input.setText(models.get("video_classification") or "")
            self.tag_model_input.setText(models.get("tag_generation") or "")
            self.desc_model_input.setText(models.get("content_description") or "")
        finally:
            self._suppress_provider_signals = was_suppressed
        self._refresh_model_pickers()

    def _refresh_provider_list(self, select_id: str = None) -> None:
        if not hasattr(self, "provider_list"):
            return
        ensure_providers(self.settings)
        providers = list_providers(self.settings)
        current_id = str(self.settings.get("current_provider_id") or "")
        target = select_id or self._selected_provider_id() or current_id
        if target not in {p["id"] for p in providers} and providers:
            target = providers[0]["id"]

        was_suppressed = self._suppress_provider_signals
        self._suppress_provider_signals = True
        try:
            self.provider_list.clear()
            select_row = 0
            for i, p in enumerate(providers):
                name = p.get("display_name") or "未命名"
                mark = " ★默认回退" if p["id"] == current_id else ""
                item = QListWidgetItem(f"{name}{mark}")
                item.setData(Qt.UserRole, p["id"])
                self.provider_list.addItem(item)
                if p["id"] == target:
                    select_row = i
            if self.provider_list.count() > 0:
                self.provider_list.setCurrentRow(select_row)
            cur = resolve_current_provider(self.settings)
            self.current_provider_label.setText(
                f"默认回退连接：{cur.get('display_name') or cur.get('id')}"
            )
            self.add_provider_btn.setEnabled(len(providers) < MAX_PROVIDERS)
            self.del_provider_btn.setEnabled(len(providers) > 1)
        finally:
            self._suppress_provider_signals = was_suppressed

        if hasattr(self, "model_provider_combo"):
            self.model_provider_combo.blockSignals(True)
            self.model_provider_combo.clear()
            for provider in providers:
                self.model_provider_combo.addItem(provider.get("display_name") or provider["id"], provider["id"])
            self.model_provider_combo.setCurrentIndex(max(0, self.model_provider_combo.findData(target)))
            self.model_provider_combo.blockSignals(False)
        if target:
            self._load_provider_form(target)
        self._suppress_provider_signals = was_suppressed
        self._load_task_routes_ui()
        self._refresh_effective_config_summary()

    def _refresh_provider_labels(self):
        """仅同步连接名称，保留选择、在编模型和密钥显示状态。"""
        providers = {provider["id"]: provider for provider in list_providers(self.settings)}
        current_id = self.settings.get("current_provider_id")
        for index in range(self.provider_list.count()):
            item = self.provider_list.item(index)
            provider_id = item.data(Qt.UserRole)
            provider = providers.get(provider_id, {})
            mark = " ★默认回退" if provider_id == current_id else ""
            item.setText(f"{provider.get('display_name') or '未命名'}{mark}")
        current = providers.get(current_id, {})
        self.current_provider_label.setText(f"默认回退连接：{current.get('display_name') or current_id}")
        combos = [self.model_provider_combo, *(widgets["provider"] for widgets in self._route_widgets.values())]
        for combo in combos:
            blocked = combo.blockSignals(True)
            try:
                for index in range(combo.count()):
                    provider_id = combo.itemData(index)
                    if provider_id in providers:
                        combo.setItemText(index, providers[provider_id].get("display_name") or provider_id)
            finally:
                combo.blockSignals(blocked)

    def _select_model_provider(self, *_):
        if self._suppress_provider_signals:
            return
        pid = self.model_provider_combo.currentData()
        for index in range(self.provider_list.count()):
            if self.provider_list.item(index).data(Qt.UserRole) == pid:
                self.provider_list.setCurrentRow(index)
                break

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
            pid = str(current.data(Qt.UserRole) or "")
            self._load_provider_form(pid)
            self.model_provider_combo.blockSignals(True)
            self.model_provider_combo.setCurrentIndex(max(0, self.model_provider_combo.findData(pid)))
            self.model_provider_combo.blockSignals(False)
        self._schedule_ai_settings_save()

    def _on_set_current_provider(self):
        self._flush_provider_form_to_settings()
        pid = self._selected_provider_id()
        if pid:
            set_current_provider(self.settings, pid)
            self._refresh_provider_list(select_id=pid)
            self._schedule_settings_save()

    def _on_add_provider(self):
        self._flush_provider_form_to_settings()
        try:
            presets = self._provider_presets()
            preset_id = self.provider_preset_combo.currentData() if hasattr(self, "provider_preset_combo") else "custom"
            preset = presets.get(preset_id, {})
            if not preset and hasattr(self, "provider_preset_combo"):
                preset = next(
                    (item for item in presets.values() if item["label"] == self.provider_preset_combo.currentText()),
                    {},
                )
            _, provider = add_provider(
                self.settings,
                display_name=preset.get("label") or f"供应商{len(list_providers(self.settings)) + 1}",
                base_url=preset.get("base_url", ""),
                set_as_current=False,
            )
            self._refresh_provider_list(select_id=provider["id"])
            if preset:
                # 刷新表单后再次写入，避免旧表单的自动保存覆盖新档案初始值。
                updated = update_provider(
                    self.settings,
                    provider["id"],
                    display_name=preset.get("label"),
                    base_url=preset.get("base_url"),
                )
                current_item = self.provider_list.currentItem()
                if current_item is not None and current_item.data(Qt.UserRole) == provider["id"]:
                    current_item.setText(updated.get("display_name") or provider["id"])
                self._load_provider_form(provider["id"])
            self._schedule_ai_settings_save()
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
            self._schedule_ai_settings_save()
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
            "<br><span style='color:#888;'>编辑后会自动保存；"
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
        if self._local_prompt_edits:
            self.register_setting_widget("local_prompt", next(iter(self._local_prompt_edits.values())))
        if self._tag_ai_prompt_edits:
            self.register_setting_widget("tag_ai_prompts", next(iter(self._tag_ai_prompt_edits.values())))
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

        hint = QLabel("停止输入 800ms 后自动保存；界面偏好立即生效，底部显示保存结果。")
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
        self.register_setting_widget("thumbnail_size", self.thumb_w_spin)
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

        # 2. XMP 导出设置
        xmp_group = QGroupBox("XMP 元数据与分析副作用")
        xmp_group.setProperty("settings_reset_group", "xmp_export")
        xmp_form = QFormLayout(xmp_group)

        self.xmp_lang_combo = QComboBox()
        self.xmp_lang_combo.addItems(["zh-CN", "en"])
        cur_lang = SettingsManager.get_setting(self.service.tag_config, "global_settings.language", "zh-CN")
        self.xmp_lang_combo.setCurrentText(cur_lang)

        self.xmp_hierarchical_cb = QCheckBox("启用层级标签 (Parent|Child)")
        self.xmp_hierarchical_cb.setChecked(SettingsManager.get_setting(self.service.tag_config, "global_settings.export_schemes.xmp_hierarchical", True))

        self.xmp_prefix_cat_cb = QCheckBox("在标签前增加分类前缀")
        self.xmp_prefix_cat_cb.setChecked(SettingsManager.get_setting(self.service.tag_config, "global_settings.export_schemes.xmp_prefix_category", True))

        xmp_form.addRow("导出语言:", self.xmp_lang_combo)
        self.auto_xmp_cb.setText("分析成功后自动写入 XMP 侧车")
        self.auto_xmp_cb.setToolTip("只影响后续分析成功后的文件写入；关闭时仍可在素材详情或监控任务中手动同步 XMP。")
        xmp_form.addRow(self.auto_xmp_cb)
        xmp_form.addRow(self.xmp_hierarchical_cb)
        xmp_form.addRow(self.xmp_prefix_cat_cb)
        form.addRow(xmp_group)

        # 3. ALE 导出列定义 (预览)
        ale_group = QGroupBox("ALE 导出字段")
        ale_group.setProperty("settings_reset_group", "ale_export")
        ale_layout = QVBoxLayout(ale_group)
        self.ale_columns_edit = QLineEdit(", ".join(SettingsManager.get_setting(self.service.tag_config, "global_settings.export_schemes.ale_columns", ["Name", "Keywords", "Category", "Summary"])))
        ale_layout.addWidget(QLabel("字段列表 (逗号分隔):"))
        ale_layout.addWidget(self.ale_columns_edit)
        form.addRow(ale_group)
        self.xmp_export_group = xmp_group
        self.ale_export_group = ale_group

        scroll.setWidget(container)
        layout.addWidget(scroll)

        self.register_setting_widget("rename_pattern", self.pattern_input)
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
            self._schedule_settings_save()
            if not self.prefs_controller.flush_now():
                return
            self._set_save_status("默认界面布局已恢复")
            self.ui_preferences_applied.emit()
            self.interface_layout_reset.emit()
        except Exception as exc:
            self._set_save_status(f"恢复布局失败：{exc}", error=True)
            QMessageBox.critical(self, "错误", f"恢复默认界面布局失败：{exc}")

    def _on_recovery_settings_changed(self):
        if self._suppress_provider_signals:
            return
        self._manual_dirty = True
        self._schedule_settings_save()
        self._set_save_status("分析失败处理有改动，正在自动保存…")

    def _media_watch_changed(self):
        self.media_watch_page.refresh()
        self.media_watch_changed.emit()

    def _on_watch_global_changed(self) -> None:
        self.settings.setdefault("processing", {})["media_watch"] = (
            self.media_watch_page.collect_global_config()
        )
        self._schedule_settings_save()
        self._set_save_status("监控全局默认有改动，正在保存…")

    def apply_settings(self, silent=False, persist=True):
        """同步 UI 数据到内存；所有写入统一通过控制器。"""
        if persist:
            self._schedule_settings_save()
            return self.prefs_controller.flush_now()
        # 模型供应商：表单 → 档案 → 任务路由 → 写穿扁平 api
        if self._provider_ui_ready:
            self._flush_provider_form_to_settings()
            self._flush_task_routes_to_settings()
            ensure_providers(self.settings)

        # 先验证恢复配置，避免无效备用 API 进入自动保存队列。
        try:
            recovery_policy = self.http400_settings.policy()
        except ValueError as exc:
            self._set_save_status(f"400 恢复设置未保存：{exc}", error=True)
            if not silent:
                QMessageBox.warning(self, "请检查400恢复设置", str(exc))
            return False

        self.settings.setdefault("processing", {})["http400_recovery"] = recovery_policy
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

        if hasattr(self, "media_watch_page"):
            self.settings.setdefault("processing", {})["media_watch"] = self.media_watch_page.collect_global_config()

        # Prompt 全中心：system + local + 标签库 AI 模板；旧 prompts.* 不再写入
        if hasattr(self, "system_prompt_edit"):
            system_prompt = self.system_prompt_edit.toPlainText().strip()
            if not persist:
                self.service.tag_config.setdefault("global_settings", {})["system_prompt"] = system_prompt
            elif hasattr(self.service, "set_system_prompt"):
                self.service.set_system_prompt(system_prompt)
            else:
                self.service.tag_config.setdefault("global_settings", {})["system_prompt"] = system_prompt
                self.service.save_tag_config(self.service.tag_config)
        if getattr(self, "_local_prompt_edits", None):
            for gid, edit in self._local_prompt_edits.items():
                if not persist:
                    for group in self.service.tag_config.get("tag_groups", []):
                        if str(group.get("id")) == str(gid):
                            group.setdefault("rules", {})["local_prompt"] = edit.toPlainText()
                            break
                elif hasattr(self.service, "set_group_local_prompt"):
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
                prefs = self.settings.setdefault("ui_preferences", {})
                prefs["last_work_scope"] = self.service.get_work_scope_paths()
                prefs["last_work_scope_exclusions"] = list(getattr(self.service, "_work_scope_exclusions", set()))

        # V6.0 Export Templates：重命名模式以「常规」页签入口为准
        if hasattr(self, "pattern_input"):
            SettingsManager.update_setting(self.settings, "rename_pattern", self.pattern_input.text().strip())
        SettingsManager.update_setting(self.service.tag_config, "global_settings.language", self.xmp_lang_combo.currentText())
        SettingsManager.update_setting(self.service.tag_config, "global_settings.export_schemes.xmp_hierarchical", self.xmp_hierarchical_cb.isChecked())
        SettingsManager.update_setting(self.service.tag_config, "global_settings.export_schemes.xmp_prefix_category", self.xmp_prefix_cat_cb.isChecked())

        ale_cols = [c.strip() for c in self.ale_columns_edit.text().split(",") if c.strip()]
        SettingsManager.update_setting(self.service.tag_config, "global_settings.export_schemes.ale_columns", ale_cols)

        if not persist:
            return True

    def reset_to_defaults(self):
        reply = QMessageBox.question(
            self, "恢复全部默认值",
            "恢复界面、分析、失败处理、模型分配、提示词、导出和监控规则？\n"
            "保留连接档案与密钥、监控目录及启用状态、数据目录、数据库和素材。"
            "监控任务的专属覆盖会恢复为继承全局。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._suppress_provider_signals = True
        try:
            for group in ("everyday", "video_analysis", "ordinary_retries", "http400_recovery",
                          "model_assignment", "prompts", "rename_template", "xmp_export", "ale_export",
                          "monitor_defaults"):
                self._apply_reset_group(group)
            from core.watch_config import DEFAULTS
            for task in self.settings["processing"]["media_watch"]["directories"]:
                for key in DEFAULTS:
                    task[key] = None
        finally:
            self._suppress_provider_signals = False
        self._schedule_settings_save()
