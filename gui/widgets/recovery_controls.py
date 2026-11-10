"""400恢复设置与手动恢复弹窗；普通用户无需编辑JSON。"""
import copy
import json
import os

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QDialogButtonBox, QScrollArea,
    QMessageBox, QAbstractItemView, QGroupBox, QToolButton, QStackedWidget, QHeaderView, QSizePolicy, QLayout, QPlainTextEdit,
)
from core.analysis_recovery import DEFAULT_POLICY, effective_policy, validate_policy, RecoveryStore, STATE_LABELS
from core.analysis_failures import FailureQuery, CATEGORIES, STAGES, validate_manual_options, failure_state_label, failure_time_range, classify
from core.model_providers import list_providers, find_provider, resolve_task_route
from gui.widgets.model_picker import ModelPicker


FIELD_LABELS = {
    "enabled": "自动重试", "primary_extra_attempts": "主 API 额外重试",
    "delay_mode": "等待方式", "delay_seconds": "起始 / 固定间隔",
    "delay_cap_seconds": "最长等待间隔", "jitter": "等待时间浮动",
    "fallback_timing": "启用备用 API 的时机", "fallback_attempts": "每个备用 API 默认尝试",
    "fallback_concurrency": "备用 API 同时处理视频数", "request_budget": "单视频最多追加请求",
    "fallbacks": "备用 API 顺序", "exclusions": "不自动重试的错误",
}
TASK_NAMES = {"video_classification": "视频主分析", "content_description": "音频转录"}


class RecoveryPolicyEditor(QWidget):
    changed = Signal()

    def __init__(self, settings, override=None, *, base=None, sources=None, allow_inherit=True, provider_resolver=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.base = copy.deepcopy(base or DEFAULT_POLICY)
        self.sources = sources or dict.fromkeys(DEFAULT_POLICY, "默认")
        self.allow_inherit = allow_inherit
        self.provider_resolver = provider_resolver or (lambda pid: find_provider(self.settings, pid))
        self.fields = {}
        self.inherit = {}
        self._loading = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        hint = QLabel("勾选“沿用上级”使用已有设置，取消勾选即可单独调整。备用 API 列表单独设置后会整体替换上级列表，清空表示不使用备用 API。", self)
        hint.setWordWrap(True)
        hint.setVisible(allow_inherit)
        layout.addWidget(hint)
        self.sections = {}
        for title in ("自动重试", "备用 API"):
            group = QGroupBox(title)
            group_layout = QFormLayout(group)
            group_layout.setSizeConstraint(QLayout.SetMinimumSize)
            group_layout.setVerticalSpacing(10)
            self.sections[title] = group_layout
            layout.addWidget(group)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("高级选项：等待、并发与排除规则")
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        layout.addWidget(self.advanced_toggle)
        self.advanced_body = QGroupBox("高级选项")
        advanced_form = QFormLayout(self.advanced_body)
        advanced_form.setSizeConstraint(QLayout.SetMinimumSize)
        advanced_form.setVerticalSpacing(10)
        self.sections["高级选项"] = advanced_form
        self.advanced_body.hide()
        self.advanced_toggle.toggled.connect(self._show_advanced)
        layout.addWidget(self.advanced_body)
        ranges = {"primary_extra_attempts": (0, 10), "fallback_attempts": (1, 5),
                  "fallback_concurrency": (1, 512), "request_budget": (1, 50),
                  "delay_seconds": (0, 3600), "delay_cap_seconds": (0, 3600), "jitter": (0, 1)}
        field_order = ("enabled", "primary_extra_attempts", "request_budget", "fallbacks", "fallback_timing", "fallback_attempts",
                       "delay_mode", "delay_seconds", "delay_cap_seconds", "jitter", "fallback_concurrency", "exclusions")
        for key in field_order:
            section = "自动重试" if key in ("enabled", "primary_extra_attempts", "request_budget") else "备用 API" if key in ("fallbacks", "fallback_timing", "fallback_attempts") else "高级选项"
            form = self.sections[section]
            if key in ("fallbacks", "exclusions"):
                widget = QTableWidget(0, 4 if key == "fallbacks" else 3)
                widget.setHorizontalHeaderLabels(["启用", "供应商", "模型", "尝试次数"] if key == "fallbacks" else ["任务", "错误码（可不填）", "错误关键词（可不填）"])
                widget.setSelectionBehavior(QAbstractItemView.SelectRows)
                widget.setSelectionMode(QAbstractItemView.SingleSelection)
                widget.verticalHeader().hide()
                widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
                widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
                widget.horizontalHeader().setStretchLastSection(True)
                widget.setFixedHeight(145)
                widget.setWordWrap(False)
                if key == "exclusions":
                    widget.setToolTip("同一行的已填条件必须全部满足；任意一行匹配即排除自动恢复。关键词不区分大小写。")
                widget.itemChanged.connect(self._changed)
            elif key == "enabled":
                widget = QCheckBox("遇到 HTTP 400 时自动重试")
                widget.toggled.connect(self._changed)
            elif key in ("delay_mode", "fallback_timing"):
                widget = QComboBox()
                choices = (("逐次延长等待", "exponential"), ("固定间隔", "fixed")) if key == "delay_mode" else (("本批分析及普通补跑结束后", "after_batch"), ("主 API 重试结束后立即开始", "immediate"))
                for text, value in choices:
                    widget.addItem(text, value)
                widget.currentIndexChanged.connect(self._changed)
            else:
                widget = QDoubleSpinBox() if key in ("delay_seconds", "delay_cap_seconds", "jitter") else QSpinBox()
                widget.setRange(*ranges[key])
                widget.setMaximumWidth(200)
                if key in ("delay_seconds", "delay_cap_seconds"):
                    widget.setSuffix(" 秒")
                elif key != "jitter":
                    widget.setSuffix(" 次" if key != "fallback_concurrency" else " 条")
                if key == "jitter":
                    widget.setRange(0, 100)
                    widget.setSuffix(" %")
                    widget.setSingleStep(5)
                widget.valueChanged.connect(self._changed)
            inherit = QCheckBox("沿用上级", self)
            inherit.setVisible(allow_inherit)
            self.fields[key] = widget
            self.inherit[key] = inherit
            inherit.setToolTip("当前上级来源：" + self.sources.get(key, "默认"))
            widget.setToolTip(widget.toolTip() or FIELD_LABELS[key])
            inherit.toggled.connect(lambda checked, k=key: self._toggle(k, checked))
            if key in ("fallbacks", "exclusions"):
                table_heading = QHBoxLayout()
                table_heading.addWidget(QLabel(FIELD_LABELS[key]))
                table_heading.addStretch()
                table_heading.addWidget(inherit)
                form.addRow(table_heading)
                form.addRow(widget)
            else:
                row = QHBoxLayout()
                row.addWidget(widget, 1)
                row.addWidget(inherit)
                form.addRow(FIELD_LABELS[key], row)
            if key in ("fallbacks", "exclusions"):
                buttons = QHBoxLayout()
                for text, action in (("添加", lambda _=False, k=key: self.add_row(k)),
                                     ("移除选中", lambda _=False, k=key: self.remove_row(k)),
                                     ("上移", lambda _=False, k=key: self.move_row(k, -1)),
                                     ("下移", lambda _=False, k=key: self.move_row(k, 1))):
                    button = QPushButton(text)
                    button.clicked.connect(action)
                    buttons.addWidget(button)
                buttons.addStretch()
                form.addRow(buttons)
        self.sections["备用 API"].addRow(QLabel("按列表从上到下尝试，成功即停止。未添加时，仅重试主 API。"))
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.PlainText)
        layout.addWidget(self.preview)
        self.details_toggle = QCheckBox("查看生效值与来源")
        self.details_toggle.setProperty("settings_view_only", True)
        layout.addWidget(self.details_toggle)
        self.effective_details = QLabel()
        self.effective_details.setTextFormat(Qt.PlainText)
        self.effective_details.setWordWrap(True)
        self.effective_details.hide()
        self.details_toggle.toggled.connect(self.effective_details.setVisible)
        layout.addWidget(self.effective_details)
        self.load_override(override or {})
        self._loading = False
        self._changed()

    def _show_advanced(self, visible):
        self.advanced_body.setVisible(visible)
        self.advanced_toggle.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)

    def _toggle(self, key, checked):
        self.fields[key].setEnabled(not checked)
        if checked:
            self._set_value(key, self.base[key])
        self._changed()

    def _set_value(self, key, value):
        widget = self.fields[key]
        if key in ("fallbacks", "exclusions"):
            widget.setRowCount(0)
            for item in value:
                self.add_row(key, item)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, QComboBox):
            widget.setCurrentIndex(widget.findData(value))
        else:
            widget.setValue(value * 100 if key == "jitter" else value)

    def load_override(self, override):
        loading = self._loading
        self._loading = True
        for key in DEFAULT_POLICY:
            self.inherit[key].setChecked(self.allow_inherit and key not in override)
            self._set_value(key, override.get(key, self.base[key]))
            self.fields[key].setEnabled(not self.inherit[key].isChecked())
        self._loading = loading

    def add_row(self, key, entry=None):
        if entry is None and self.inherit[key].isChecked():
            self.inherit[key].setChecked(False)
        entry = entry or {}
        table = self.fields[key]
        row = table.rowCount()
        table.insertRow(row)
        if key == "fallbacks":
            enabled = QCheckBox()
            enabled.setChecked(entry.get("enabled", True))
            enabled.toggled.connect(self._changed)
            provider = QComboBox()
            for p in list_providers(self.settings):
                provider.addItem(p.get("display_name") or p["id"], p["id"])
            if entry.get("provider_id"):
                index = provider.findData(entry["provider_id"])
                if index < 0:
                    provider.addItem("已失效：" + entry["provider_id"], entry["provider_id"])
                    index = provider.count() - 1
                provider.setCurrentIndex(index)
            provider.currentIndexChanged.connect(self._changed)
            model = ModelPicker(lambda combo=provider: self.provider_resolver(combo.currentData()), entry.get("model", ""), compact=True)
            model.setPlaceholderText("选择或输入支持该任务的模型")
            model.textChanged.connect(self._changed)
            provider.currentIndexChanged.connect(model.refresh_connection)
            attempts = QSpinBox()
            attempts.setRange(0, 5)
            attempts.setSpecialValueText("默认")
            attempts.setValue(entry.get("attempts", 0))
            attempts.valueChanged.connect(self._changed)
            for col, widget in enumerate((enabled, provider, model, attempts)):
                table.setCellWidget(row, col, widget)
            table.setRowHeight(row, 40)
        else:
            task = QComboBox()
            task.addItem("不限任务", "")
            for task_key, label in TASK_NAMES.items():
                task.addItem(label, task_key)
            selected_task = entry.get("task_key", "")
            if task.findData(selected_task) < 0:
                task.addItem(f"未知任务（{selected_task}）", selected_task)
            task.setCurrentIndex(task.findData(selected_task))
            task.currentIndexChanged.connect(self._changed)
            table.setCellWidget(row, 0, task)
            for col, field in ((1, "error_code"), (2, "keyword")):
                table.setItem(row, col, QTableWidgetItem(entry.get(field, "")))
        self._changed()

    def rows(self, key):
        table = self.fields[key]
        result = []
        for row in range(table.rowCount()):
            if key == "fallbacks":
                item = {"enabled": table.cellWidget(row, 0).isChecked(), "provider_id": table.cellWidget(row, 1).currentData(), "model": table.cellWidget(row, 2).text().strip()}
                attempts = table.cellWidget(row, 3).value()
                if attempts:
                    item["attempts"] = attempts
            else:
                item = {field: table.item(row, col).text().strip() for col, field in ((1, "error_code"), (2, "keyword")) if table.item(row, col) and table.item(row, col).text().strip()}
                task = table.cellWidget(row, 0)
                if task and task.currentData():
                    item["task_key"] = task.currentData()
            result.append(item)
        return result

    def remove_row(self, key):
        if not self.inherit[key].isChecked():
            self.fields[key].removeRow(self.fields[key].currentRow())
            self._changed()

    def move_row(self, key, direction):
        if self.inherit[key].isChecked():
            return
        table = self.fields[key]
        index = table.currentRow()
        target = index + direction
        if index < 0 or not 0 <= target < table.rowCount():
            return
        values = self.rows(key)
        values[index], values[target] = values[target], values[index]
        self._set_value(key, values)
        table.selectRow(target)
        self._changed()

    def override(self):
        result = {}
        for key, widget in self.fields.items():
            if self.inherit[key].isChecked():
                continue
            if key in ("fallbacks", "exclusions"):
                result[key] = self.rows(key)
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            else:
                result[key] = widget.value() / 100 if key == "jitter" else widget.value()
        return result

    def effective(self):
        return {**copy.deepcopy(self.base), **self.override()}

    def _changed(self, *_):
        if self._loading:
            return
        policy = self.effective()
        names = {p["id"]: p.get("display_name") or p["id"] for p in list_providers(self.settings)}
        targets = [f"{names.get(x.get('provider_id'), '失效供应商')} / {x.get('model') or '未填模型'}" for x in policy["fallbacks"] if x.get("enabled", True)]
        status = "已开启自动重试" if policy["enabled"] else "已关闭自动重试（仍可手动重试）"
        self.preview.setText(f"{status}。首次 400 后，主 API 额外重试 {policy['primary_extra_attempts']} 次，所有恢复请求合计最多 {policy['request_budget']} 次（含兼容重发）。\n可能接收视频帧或音频的 API：主 API" + (" → " + " → ".join(targets) if targets else "；未配置备用 API。"))
        display = {"enabled": "开启" if policy["enabled"] else "关闭",
                   "delay_mode": "逐次延长等待" if policy["delay_mode"] == "exponential" else "固定间隔",
                   "fallback_timing": "本批分析结束后" if policy["fallback_timing"] == "after_batch" else "主 API 重试结束后",
                   "jitter": f"{policy['jitter'] * 100:g}%", "fallbacks": " → ".join(targets) or "无",
                   "exclusions": f"{len(policy['exclusions'])} 条规则"}
        self.effective_details.setText("\n".join(f"{FIELD_LABELS[k]}：{display.get(k, policy[k])}（{self.sources.get(k, '默认') if self.inherit[k].isChecked() else '当前设置'}）" for k in DEFAULT_POLICY))
        self.changed.emit()


class RecoverySettingsPanel(QWidget):
    """主设置内的编辑区；只返回配置，由外层统一保存。"""
    changed = Signal()

    def __init__(self, service, parent=None, *, provider_resolver=None):
        super().__init__(parent)
        self.service = service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("HTTP 400 使用独立恢复规则，不叠加上面的普通重试次数。先重试主 API，再按顺序使用备用 API；只影响下一轮分析。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scope_row = QFormLayout()
        self.scope = QComboBox()
        self.scope.setProperty("settings_view_only", True)
        for label, key in (("所有分析任务（默认设置）", "global"), ("视频主分析（单独设置）", "video_classification"), ("音频转录（单独设置）", "content_description")):
            self.scope.addItem(label, key)
        scope_row.addRow("设置范围", self.scope)
        layout.addLayout(scope_row)
        self.scope_hint = QLabel()
        self.scope_hint.setWordWrap(True)
        layout.addWidget(self.scope_hint)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        root = service.settings.get("processing", {}).get("http400_recovery", {})
        self.global_editor = RecoveryPolicyEditor(service.settings, root.get("global", {}), allow_inherit=False, provider_resolver=provider_resolver)
        base = self.global_editor.effective()
        sources = {k: "全局" if k in self.global_editor.override() else "默认" for k in DEFAULT_POLICY}
        self.task_editor = RecoveryPolicyEditor(service.settings, root.get("tasks", {}).get("video_classification", {}), base=base, sources=sources, provider_resolver=provider_resolver)
        self.audio_editor = RecoveryPolicyEditor(service.settings, root.get("tasks", {}).get("content_description", {}), base=base, sources=sources, provider_resolver=provider_resolver)
        for editor in (self.global_editor, self.task_editor, self.audio_editor):
            self.stack.addWidget(editor)
            editor.changed.connect(self.changed)
        self.global_editor.changed.connect(self._sync_parent)
        self.scope.currentIndexChanged.connect(self._switch_scope)
        self._switch_scope(0)

    def _switch_scope(self, index):
        self.stack.setCurrentIndex(index)
        hints = (
            "默认设置适用于所有视频分析任务；只有需要不同规则时，才切换到单独设置。",
            "视频主分析一次生成分类、标签和摘要。勾选“沿用上级”的字段跟随默认设置。",
            "仅在开启音频转录时生效。备用模型须支持音频转录；未单独设置的字段跟随默认设置。",
        )
        self.scope_hint.setText(hints[index])
        # 避免隐藏的高级选项或其他范围把整页撑成过长表单。
        for i in range(self.stack.count()):
            editor = self.stack.widget(i)
            policy = editor.sizePolicy()
            policy.setVerticalPolicy(QSizePolicy.Preferred if i == index else QSizePolicy.Ignored)
            editor.setSizePolicy(policy)
        self.stack.updateGeometry()

    def _sync_parent(self):
        for editor in (self.task_editor, self.audio_editor):
            override = editor.override()
            editor.base = self.global_editor.effective()
            editor.sources = {k: "全局" if k in self.global_editor.override() else "默认" for k in DEFAULT_POLICY}
            for key in DEFAULT_POLICY:
                editor.inherit[key].setToolTip("当前上级来源：" + editor.sources[key])
            editor.load_override(override)
            editor._changed()

    def policy(self):
        settings = self.service.settings
        result = copy.deepcopy(settings.get("processing", {}).get("http400_recovery", {}))
        result["global"] = validate_policy(self.global_editor.override(), settings)
        tasks = result.setdefault("tasks", {})
        tasks["video_classification"] = validate_policy(self.task_editor.override(), settings)
        tasks["content_description"] = validate_policy(self.audio_editor.override(), settings)
        return result


class RecoverySettingsDialog(QDialog):
    """兼容独立入口，共用主页面表单与校验。"""
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("HTTP 400 重试与备用 API")
        self.resize(980, 800)
        layout = QVBoxLayout(self)
        self.panel = RecoverySettingsPanel(service)
        self.global_editor = self.panel.global_editor
        self.task_editor = self.panel.task_editor
        self.audio_editor = self.panel.audio_editor
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        from core.video_organizer_service import SettingsManager
        settings = self.service.settings
        old = copy.deepcopy(settings.get("processing", {}).get("http400_recovery"))
        try:
            settings.setdefault("processing", {})["http400_recovery"] = self.panel.policy()
            SettingsManager.save_settings(settings, self.service.db)
        except Exception as exc:
            if old is None:
                settings.get("processing", {}).pop("http400_recovery", None)
            else:
                settings["processing"]["http400_recovery"] = old
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        super().accept()


class VideoRecoverySettingsDialog(QDialog):
    """保存专属策略与发起请求分开，已恢复视频也能调整策略。"""
    def __init__(self, service, path, parent=None):
        super().__init__(parent)
        self.service, self.path = service, path
        self.setWindowTitle("此视频的400恢复策略")
        self.resize(980, 800)
        layout = QVBoxLayout(self)
        current = service.get_recovery_detail(path) or {}
        task = current.get("detail", {}).get("last_error", {}).get("task_key") or "video_classification"
        base, sources = effective_policy(service.settings, task)
        self.editor = RecoveryPolicyEditor(service.settings, RecoveryStore(service.db).override(path), base=base, sources=sources)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存专属策略（不发起请求）")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            override = validate_policy(self.editor.override(), self.service.settings)
            self.service.save_recovery_override([self.path], override)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        super().accept()


class ManualRecoveryDialog(QDialog):
    def __init__(self, service, paths, parent=None):
        super().__init__(parent)
        self.service, self.paths = service, paths
        self.setWindowTitle(f"重试400失败项（{len(paths)}条）")
        self.resize(980, 800)
        layout = QVBoxLayout(self)
        hint = QLabel("每次手动重试开启新的有限轮次并跳过旧缓存。指定API可分别设置视频主分析和音频转录；未覆盖的策略字段继承各视频设置。启动前将预览全部目标与请求上限。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.mode = QComboBox()
        for text, value in (("按有效策略", "policy"), ("仅主API", "primary"), ("指定API（自定义供应商和模型）", "selected")):
            self.mode.addItem(text, value)
        layout.addWidget(self.mode)
        route_help = QHBoxLayout()
        self.route_hint = QLabel()
        self.route_hint.setWordWrap(True)
        self.edit_routes = QPushButton("更改供应商和模型")
        self.edit_routes.clicked.connect(lambda: self.mode.setCurrentIndex(self.mode.findData("selected")))
        route_help.addWidget(self.route_hint, 1)
        route_help.addWidget(self.edit_routes)
        layout.addLayout(route_help)
        route_row = QHBoxLayout()
        route_row.addWidget(QLabel("视频主分析连接"))
        self.provider = QComboBox()
        for p in list_providers(service.settings):
            self.provider.addItem(p.get("display_name") or p["id"], p["id"])
        self.model = ModelPicker(lambda: find_provider(service.settings, self.provider.currentData()))
        video_route = resolve_task_route(service.settings, "video_classification")
        self.provider.setCurrentIndex(self.provider.findData(video_route["provider_id"]))
        self.model.setText(video_route["model"])
        self.model.refresh_connection()
        self.model.setPlaceholderText("选择视频模型，或手动输入模型名")
        self.provider.currentIndexChanged.connect(self.model.refresh_connection)
        route_row.addWidget(self.provider)
        route_row.addWidget(self.model)
        self.video_attempts = QSpinBox()
        self.video_attempts.setRange(0, 5)
        self.video_attempts.setSpecialValueText("沿用策略")
        self.video_attempts.setSuffix(" 次")
        route_row.addWidget(QLabel("尝试上限"))
        route_row.addWidget(self.video_attempts)
        layout.addLayout(route_row)
        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("音频转录连接"))
        self.audio_provider = QComboBox()
        for p in list_providers(service.settings):
            self.audio_provider.addItem(p.get("display_name") or p["id"], p["id"])
        self.audio_model = ModelPicker(lambda: find_provider(service.settings, self.audio_provider.currentData()))
        self.audio_model.setText("whisper-1")
        audio_route = resolve_task_route(service.settings, "content_description")
        self.audio_provider.setCurrentIndex(self.audio_provider.findData(audio_route["provider_id"]))
        self.audio_model.refresh_connection()
        self.audio_provider.currentIndexChanged.connect(self.audio_model.refresh_connection)
        audio_row.addWidget(self.audio_provider)
        audio_row.addWidget(self.audio_model)
        self.audio_attempts = QSpinBox()
        self.audio_attempts.setRange(0, 5)
        self.audio_attempts.setSpecialValueText("沿用策略")
        self.audio_attempts.setSuffix(" 次")
        audio_row.addWidget(QLabel("尝试上限"))
        audio_row.addWidget(self.audio_attempts)
        layout.addLayout(audio_row)
        self.audio_hint = QLabel("音频转录未启用，本次不会请求音频接口。可在分析设置中开启。")
        self.audio_hint.setWordWrap(True)
        layout.addWidget(self.audio_hint)
        pace = QHBoxLayout()
        self.concurrency = QSpinBox(); self.concurrency.setRange(1, 32); self.concurrency.setValue(2)
        self.interval = QDoubleSpinBox(); self.interval.setRange(0, 3600); self.interval.setValue(2)
        pace.addWidget(QLabel("本次并发")); pace.addWidget(self.concurrency)
        pace.addWidget(QLabel("请求间隔（秒）")); pace.addWidget(self.interval)
        for label, value in (("串行重试", 1), ("低并发重试", 2)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, n=value: (self.concurrency.setValue(n), self.interval.setValue(2)))
            pace.addWidget(button)
        layout.addLayout(pace)
        self.mode.currentIndexChanged.connect(self._route_visibility)
        self._route_visibility()
        previous = service.get_recovery_detail(paths[0]) or {}
        task_key = previous.get("detail", {}).get("last_error", {}).get("task_key") or "video_classification"
        self.task_key = task_key
        base, sources = effective_policy(service.settings, task_key, video=RecoveryStore(service.db).override(paths[0]))
        self.editor = RecoveryPolicyEditor(service.settings, base=base, sources=sources)
        for label in self.editor.findChildren(QLabel):
            if label.text() == FIELD_LABELS["request_budget"]:
                label.setText("单视频本轮请求总上限")
        self.editor.fields["request_budget"].setToolTip("从首次请求计入，包含兼容重发及音频请求；各阶段共用本轮预算。")
        budget_hint = QLabel("本轮预算从首次请求计入，包含兼容重发和音频请求，各阶段共用。")
        budget_hint.setWordWrap(True)
        layout.addWidget(budget_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        layout.addWidget(scroll)
        self.save_video = QCheckBox("同时保存本次字段覆盖为所选视频的专属策略（其余字段继续使用已有覆盖）")
        layout.addWidget(self.save_video)
        clear = QPushButton("清除所选视频专属策略，恢复继承")
        clear.clicked.connect(self.clear_video_overrides)
        layout.addWidget(clear)
        self.options = {}
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始重试")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not isinstance(self.focusWidget(), QPushButton):
            event.accept()
            return
        super().keyPressEvent(event)

    def _route_visibility(self):
        enabled = self.mode.currentData() == "selected"
        self.route_hint.setText("本次使用下方指定连接；模型名可直接输入，开始前会展示重试预览。" if enabled else "当前连接由有效策略或主API决定。点击右侧按钮，可为本次重试分别指定视频和音频连接。")
        self.provider.setEnabled(enabled)
        self.model.setEnabled(enabled)
        self.video_attempts.setEnabled(enabled)
        audio_enabled = bool(self.service.settings.get("processing", {}).get("enable_audio_transcription"))
        self.audio_hint.setVisible(not audio_enabled)
        self.audio_provider.setEnabled(enabled and audio_enabled)
        self.audio_model.setEnabled(enabled and audio_enabled)
        self.audio_attempts.setEnabled(enabled and audio_enabled)

    def apply_manual_options(self, options):
        options = validate_manual_options(options, self.service.settings)
        self.concurrency.setValue(options["concurrency"])
        self.interval.setValue(options["request_interval"])
        routes = options["stage_routes"]
        if routes:
            self.mode.setCurrentIndex(self.mode.findData("selected"))
        for task, provider, model, attempts in (("video_classification", self.provider, self.model, self.video_attempts),
                                                ("content_description", self.audio_provider, self.audio_model, self.audio_attempts)):
            if task in routes:
                provider.setCurrentIndex(provider.findData(routes[task]["provider_id"]))
                model.setText(routes[task]["model"])
                attempts.setValue(routes[task].get("attempts", 0))

    def clear_video_overrides(self):
        if QMessageBox.question(self, "清除专属策略", f"将清除所选{len(self.paths)}条视频的专属恢复策略，是否继续？") != QMessageBox.Yes:
            return
        try:
            self.service.save_recovery_override(self.paths, {})
        except Exception as exc:
            QMessageBox.warning(self, "清除专属策略失败", "未能清除，请修正写入问题后重试。\n" + str(exc))
            return
        self.editor.base, self.editor.sources = effective_policy(self.service.settings, self.task_key)
        self.editor.load_override({})
        self.editor._changed()
        QMessageBox.information(self, "已恢复继承", f"已清除所选{len(self.paths)}条视频的专属策略；尚未发起重试。")

    def accept(self):
        try:
            override = validate_policy(self.editor.override(), self.service.settings)
            selected = None
            if self.mode.currentData() == "selected":
                selected = {"provider_id": self.provider.currentData(), "model": self.model.text().strip()}
                if self.video_attempts.value():
                    selected["attempts"] = self.video_attempts.value()
                validate_policy({"fallbacks": [selected]}, self.service.settings)
            manual = {"concurrency": self.concurrency.value(), "request_interval": self.interval.value()}
            if selected:
                manual["stage_routes"] = {"video_classification": selected}
                if self.service.settings.get("processing", {}).get("enable_audio_transcription"):
                    manual["stage_routes"]["content_description"] = {"provider_id": self.audio_provider.currentData(), "model": self.audio_model.text().strip()}
                    if self.audio_attempts.value():
                        manual["stage_routes"]["content_description"]["attempts"] = self.audio_attempts.value()
            self.options = {"recovery_mode": self.mode.currentData(), "recovery_override": override, "selected_fallback": selected, "manual_options": validate_manual_options(manual, self.service.settings)}
            preview = self.service.preview_manual_retry(tuple(self.paths), **self.options)
            if not preview["paths"]:
                raise ValueError("没有可执行项目：\n" + "\n".join(x["reason"] for x in preview["skipped"]))
            lines = [f"可执行 {len(preview['paths'])} 条，跳过 {len(preview['skipped'])} 条；请求上限 {preview['request_limit']} 次", f"模式：{self.mode.currentText()}；并发 {manual['concurrency']}；间隔 {manual['request_interval']} 秒"]
            for item in preview["items"]:
                stages = []
                for task, chain in item["route_chains"].items():
                    stages.append(TASK_NAMES[task] + "：" + " → ".join(
                        f"{entry['display_name'] or entry['provider_id']} / {entry['model']}（最多{entry['attempt_limit']}次）" for entry in chain))
                lines.append(os.path.basename(item["path"]) + "：" + "；".join(stages) + f"；各阶段共用{item['budget']}次预算")
            lines.extend(os.path.basename(x["path"]) + "：跳过，" + x["reason"] for x in preview["skipped"])
            confirmation = QMessageBox(QMessageBox.Question, "重试预览", "\n".join(lines[:12]), QMessageBox.Ok | QMessageBox.Cancel, self)
            confirmation.setDetailedText("\n".join(lines))
            confirmation.button(QMessageBox.Ok).setText("开始重试")
            if confirmation.exec() != QMessageBox.Ok:
                return
            self.paths = tuple(preview["paths"])
            self.preview = preview
            self.options["manual_options"] = preview["manual_options"]
            if self.save_video.isChecked():
                store = RecoveryStore(self.service.db)
                for path in self.paths:
                    self.service.save_recovery_override([path], {**store.override(path), **override})
        except Exception as exc:
            QMessageBox.warning(self, "设置无效", str(exc))
            return
        super().accept()


class AnalysisFailureCenterDialog(QDialog):
    diagnosis_progress = Signal(int, int)
    """统一查看失败原因、主动诊断和批量手动重试。"""
    def __init__(self, service, paths=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.scope_paths = tuple(paths) if paths is not None else None
        self.offset = 0
        self.page_size = 100
        self.worker = None
        self.retry_cancelling = False
        self.total = 0
        self.round_result = {}
        self.round_skipped = []
        self.round_failed_paths = ()
        import threading
        self.diagnosis_cancel = threading.Event()
        self.setWindowTitle("分析失败处理")
        self.resize(1180, 720)
        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.category = QComboBox(); self.category.addItem("全部原因", "")
        for key, label in CATEGORIES.items(): self.category.addItem(label, key)
        self.stage = QComboBox(); self.stage.addItem("全部阶段", "")
        for key, label in STAGES.items(): self.stage.addItem(label, key)
        self.state = QComboBox()
        for label, value in (("未解决", "unresolved"), ("全部历史", ""), ("重试已耗尽", "exhausted"), ("已中断", "interrupted"), ("已恢复", "recovered")):
            self.state.addItem(label, value)
        self.keyword = QLineEdit(); self.keyword.setPlaceholderText("错误关键词或文件名")
        for widget in (self.category, self.stage, self.state, self.keyword): toolbar.addWidget(widget)
        refresh = QPushButton("刷新"); refresh.clicked.connect(self.reload); toolbar.addWidget(refresh)
        root.addLayout(toolbar)
        filters = QFormLayout()
        self.http = QSpinBox(); self.http.setRange(0, 599); self.http.setSpecialValueText("全部HTTP")
        self.provider_filter = QLineEdit(); self.provider_filter.setPlaceholderText("供应商ID（精确匹配）")
        self.model_filter = QLineEdit(); self.model_filter.setPlaceholderText("模型（精确匹配）")
        self.since = QLineEdit(); self.since.setPlaceholderText("UTC日期或ISO时间，如2026-11-01")
        self.until = QLineEdit(); self.until.setPlaceholderText("UTC日期包含当天，如2026-11-30；也可填带时区ISO时间")
        self.min_attempts = QSpinBox(); self.min_attempts.setRange(-1, 100000); self.min_attempts.setSpecialValueText("不限"); self.min_attempts.setValue(-1)
        self.max_attempts = QSpinBox(); self.max_attempts.setRange(-1, 100000); self.max_attempts.setSpecialValueText("不限"); self.max_attempts.setValue(-1)
        for label, widget in (("HTTP", self.http), ("供应商", self.provider_filter), ("模型", self.model_filter), ("失败时间从（UTC）", self.since), ("失败时间至（UTC）", self.until), ("最少尝试", self.min_attempts), ("最多尝试", self.max_attempts)):
            filters.addRow(label, widget)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("展开高级筛选（修改后点击刷新）")
        self.advanced_toggle.setCheckable(True)
        root.addWidget(self.advanced_toggle)
        advanced_body = QGroupBox(); advanced_body.setLayout(filters)
        self.advanced = QScrollArea()
        self.advanced.setWidgetResizable(True)
        self.advanced.setWidget(advanced_body)
        self.advanced.setMinimumHeight(100)
        self.advanced.setMaximumHeight(150)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.advanced_toggle.toggled.connect(lambda checked: self.advanced_toggle.setText("收起高级筛选" if checked else "展开高级筛选（修改后点击刷新）"))
        root.addWidget(self.advanced)
        flags = QHBoxLayout()
        self.ever400 = QCheckBox("曾遇到400")
        self.retained = QCheckBox("重新分析失败但保留旧结果")
        self.all_library = QCheckBox("切换到全部素材库")
        for widget in (self.ever400, self.retained, self.all_library): flags.addWidget(widget)
        root.addLayout(flags)
        self.summary = QLabel(); self.summary.setWordWrap(True); root.addWidget(self.summary)
        self.list_summary = QLabel(); self.list_summary.setWordWrap(True); root.addWidget(self.list_summary)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["文件", "原因", "阶段", "HTTP", "状态", "供应商/模型", "尝试", "最近错误", "建议"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(140)
        self.table.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate((180, 115, 95, 55, 80, 170, 55, 240, 180)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        root.addWidget(self.table, 1)
        self.table.cellDoubleClicked.connect(lambda row, col: show_recovery_detail(self.service, self.rows[row]["path"], self))
        pages = QHBoxLayout()
        self.previous = QPushButton("上一页"); self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next = QPushButton("下一页"); self.next.clicked.connect(lambda: self.change_page(1))
        self.page_label = QLabel()
        for widget in (self.previous, self.page_label, self.next): pages.addWidget(widget)
        root.addLayout(pages)
        self.empty_label = QLabel(); root.addWidget(self.empty_label)
        self.round_summary = QLabel(); self.round_summary.setWordWrap(True); root.addWidget(self.round_summary)
        self.round_only = QCheckBox("仅显示本轮仍失败项"); self.round_only.setEnabled(False)
        self.round_only.toggled.connect(self.reload); root.addWidget(self.round_only)
        self.filter_widgets = (self.category, self.stage, self.state, self.keyword, self.http, self.provider_filter, self.model_filter, self.since, self.until, self.min_attempts, self.max_attempts, self.ever400, self.retained, self.all_library)
        actions = QHBoxLayout()
        self.action_scope = QComboBox()
        self.action_scope.addItem("选中项（0条）", "selected")
        self.action_scope.addItem("全部筛选结果（0条，含未加载分页）", "filtered")
        self.action_scope.setCurrentIndex(1)
        root.addWidget(self.action_scope)
        root.addWidget(QLabel("选中仅限当前页；翻页或刷新会清空选择。操作范围请在上方明确选择。"))
        self.detail_btn = QPushButton("查看详情"); self.detail_btn.setEnabled(False)
        self.detail_btn.clicked.connect(self._show_selected_detail); actions.addWidget(self.detail_btn)
        self.diagnose_btn = QPushButton("AI诊断相同错误组"); self.diagnose_btn.clicked.connect(self.diagnose); actions.addWidget(self.diagnose_btn)
        self.retry_btn = QPushButton("手动重试"); self.retry_btn.clicked.connect(self.retry); actions.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton("取消本轮重试"); self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_retry); actions.addWidget(self.cancel_btn)
        self.cancel_diagnosis_btn = QPushButton("取消诊断"); self.cancel_diagnosis_btn.setEnabled(False)
        self.cancel_diagnosis_btn.clicked.connect(self._cancel_diagnosis); actions.addWidget(self.cancel_diagnosis_btn)
        close = QPushButton("关闭"); close.clicked.connect(self.reject); actions.addWidget(close)
        root.addLayout(actions)
        self.table.itemSelectionChanged.connect(self._update_selection)
        self.diagnosis_progress.connect(self._diagnosis_progress)
        for widget in (self.category, self.stage, self.state): widget.currentIndexChanged.connect(self.reload)
        self.keyword.returnPressed.connect(self.reload)
        self.ever400.toggled.connect(self._ever400_changed)
        for widget in (self.retained, self.all_library): widget.toggled.connect(self.reload)
        self.reload()

    def _ever400_changed(self, checked):
        # 历史入口默认包含已恢复；用户仍可随后明确选择未解决。
        if checked and self.state.currentData() == "unresolved":
            self.state.setCurrentIndex(self.state.findData(""))
        else:
            self.reload()

    def _query(self):
        return FailureQuery(scope_paths=None if self.all_library.isChecked() else self.scope_paths, category=self.category.currentData(), stage=self.stage.currentData(), state=self.state.currentData(), keyword=self.keyword.text().strip(), status_code=self.http.value() or None, provider_id=self.provider_filter.text().strip(), model=self.model_filter.text().strip(), since=self.since.text().strip(), until=self.until.text().strip(), min_attempts=None if self.min_attempts.value() < 0 else self.min_attempts.value(), max_attempts=None if self.max_attempts.value() < 0 else self.max_attempts.value(), ever400=self.ever400.isChecked(), retained_result=self.retained.isChecked())

    def reload(self, *args, reset=True):
        for widget in self.filter_widgets:
            widget.setEnabled(not self.round_only.isChecked())
        if reset or not hasattr(self, "applied_query"):
            query = self._query()
            if self.round_only.isChecked():
                from dataclasses import replace
                query = replace(FailureQuery(), scope_paths=self.round_failed_paths)
            try:
                failure_time_range(query.since, query.until)
            except ValueError as exc:
                QMessageBox.warning(self, "筛选无效", str(exc))
                return
            self.applied_query = query
        if reset: self.offset = 0
        data = self.service.query_analysis_failures(self.applied_query, offset=self.offset, limit=self.page_size)
        self.total = data.get("total", 0)
        valid_offset = ((self.total - 1) // self.page_size) * self.page_size if self.total else 0
        if self.offset > valid_offset:
            self.offset = valid_offset
            data = self.service.query_analysis_failures(self.applied_query, offset=self.offset, limit=self.page_size)
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + self.page_size < data.get("total", 0))
        self.page_label.setText(f"第 {self.offset // self.page_size + 1 if self.total else 0} 页 / 共 {self.total} 条")
        self.empty_label.setText(("本轮仍失败项范围内没有匹配记录；成功、跳过及取消数量请查看本轮结果。" if self.round_only.isChecked() else "没有匹配的失败项，请调整筛选或范围。") if not self.total else "")
        self.rows = data.get("rows", [])
        self.table.setRowCount(0)
        for item in self.rows:
            row = self.table.rowCount(); self.table.insertRow(row)
            values = [item.get("filename") or os.path.basename(item.get("path", "")), CATEGORIES.get(item.get("category"), item.get("category")), STAGES.get(item.get("stage"), item.get("stage")), item.get("status_code") or "—", failure_state_label(item.get("state")), f"{item.get('provider_id') or '—'} / {item.get('model') or '—'}", item.get("attempt_count") if item.get("attempt_count") is not None else "—", item.get("message") or "—", item.get("suggestion") or "—"]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value)); cell.setToolTip(str(value))
                if col == 0: cell.setToolTip(item.get("path", str(value)))
                self.table.setItem(row, col, cell)
        self._update_selection()
        counts = "；".join(f"{CATEGORIES.get(k, k)} {v}" for k, v in data.get("counts", {}).items()) or "无"
        scope_label = "本轮仍失败项" if self.round_only.isChecked() else "已选/筛选结果" if self.applied_query.scope_paths is not None else "全部素材库"
        self.list_summary.setText(f"失败记录 {data.get('total', 0)} 条 · 原因统计：{counts} · 当前范围：{scope_label}")

    def change_page(self, direction):
        self.offset = max(0, self.offset + direction * self.page_size)
        self.reload(reset=False)

    def selected_paths(self):
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        if self.action_scope.currentData() == "selected":
            return [self.rows[i]["path"] for i in sorted(selected)]
        from core.analysis_failures import FailureStore
        return FailureStore(self.service.db).query(self.applied_query, paths_only=True)

    def _update_selection(self):
        count = len(self.table.selectionModel().selectedRows())
        self.action_scope.setItemText(0, f"选中项（{count}条）")
        self.action_scope.setItemText(1, f"全部筛选结果（{self.total}条，含未加载分页）")
        self.detail_btn.setEnabled(count == 1)

    def _show_selected_detail(self):
        rows = self.table.selectionModel().selectedRows()
        if len(rows) == 1:
            show_recovery_detail(self.service, self.rows[rows[0].row()]["path"], self)

    def _cancel_retry(self):
        if self.worker:
            self.retry_cancelling = True
            self.worker.request_cancel()
            self.cancel_btn.setEnabled(False); self.cancel_btn.setText("正在取消…")
            self.summary.setText("正在取消：已停止新请求，等待当前请求完成；已完成结果会保留。")

    def _retry_progress(self, percent, message):
        if not self.retry_cancelling: self.summary.setText(message)

    def _cancel_diagnosis(self):
        self.diagnosis_cancel.set()
        self.cancel_diagnosis_btn.setEnabled(False); self.cancel_diagnosis_btn.setText("正在取消…")
        self.summary.setText("正在取消诊断：等待当前请求完成，已完成组会保留。")

    def _diagnosis_progress(self, completed, total):
        if not self.diagnosis_cancel.is_set():
            self.summary.setText(f"正在诊断：已完成 {completed} / {total} 组")

    def retry(self, checked=False, *, paths=None, suggestion=None):
        lock = getattr(self.service, "_analysis_run_lock", None)
        if (self.worker and self.worker.isRunning()) or (lock and lock.locked()):
            QMessageBox.information(self, "分析正在运行", "请等待当前分析结束或先取消。"); return
        paths = tuple(paths if paths is not None else self.selected_paths())
        if not paths:
            QMessageBox.information(self, "没有目标", "当前筛选没有可重试的失败项。"); return
        dialog = ManualRecoveryDialog(self.service, paths, self)
        if suggestion is not None:
            try:
                dialog.apply_manual_options(suggestion)
            except ValueError as exc:
                QMessageBox.warning(self, "AI建议无效", str(exc)); return
        dialog.setWindowTitle(f"手动重试分析失败项（{len(paths)}条）")
        if dialog.exec() == QDialog.Accepted:
            from gui.workers.analysis_worker import AnalysisWorker
            self.round_result = {}
            self.round_summary.setText("本轮重试运行中…")
            self.round_skipped = getattr(dialog, "preview", {}).get("skipped", [])
            self.round_failed_paths = ()
            self.round_only.setChecked(False); self.round_only.setEnabled(False)
            self.worker = AnalysisWorker(self.service, list(dialog.paths), force_reanalyze=True, recovery_options=dialog.options)
            self.summary.setText("正在启动本轮重试…")
            self.retry_cancelling = False; self.cancel_btn.setText("取消本轮重试")
            self.retry_btn.setEnabled(False); self.diagnose_btn.setEnabled(False); self.cancel_btn.setEnabled(True)
            self.worker.progress_updated.connect(self._retry_progress)
            self.worker.task_finished.connect(self._retry_finished)
            self.worker.result_ready.connect(self._receive_retry_result)
            self.worker.start()
            return True
        return False

    def _receive_retry_result(self, result):
        self.round_result = result
        self.round_failed_paths = tuple(result.get("remaining_failed_paths", result.get("failed_paths", [])))
        self.round_only.setEnabled(bool(self.round_failed_paths))
        cancelled = int(result.get("cancelled_count", 0))
        failed = max(0, int(result.get("failed", 0)) - cancelled)
        skipped = len(self.round_skipped) + int(result.get("skipped", 0))
        self.round_summary.setText(f"本轮结果：成功 {result.get('succeeded', 0)}；仍失败 {failed}；跳过 {skipped}；取消 {cancelled}")

    def _retry_finished(self, ok, message):
        self.service.invalidate_video_memory_cache()
        parent = self.parent()
        while parent is not None:
            model = getattr(parent, "model", None)
            if model is not None and hasattr(model, "clear_temp_status_map"):
                model.clear_temp_status_map()
            if hasattr(parent, "load_data"):
                parent.load_data()
                break
            current = getattr(parent, "current_video", None)
            if isinstance(current, dict) and hasattr(parent, "load_video_data"):
                detail = self.service.get_video_detail(current.get("path"))
                if detail:
                    parent.load_video_data(detail)
            parent = parent.parent()
        self.retry_btn.setEnabled(True); self.diagnose_btn.setEnabled(True); self.cancel_btn.setEnabled(False)
        self.retry_cancelling = False; self.cancel_btn.setText("取消本轮重试")
        self.summary.setText("本轮重试已结束。" + message)
        if not self.round_result:
            self.round_summary.setText("本轮未返回完整统计：" + message)
        self.reload(reset=False)
        QMessageBox.information(self, "本轮重试结束", message)

    def reject(self):
        io_worker = getattr(self, "_io_worker", None)
        if io_worker and io_worker.isRunning():
            self._cancel_diagnosis()
            return
        if self.worker and self.worker.isRunning():
            self._cancel_retry()
            return
        super().reject()

    def closeEvent(self, event):
        io_worker = getattr(self, "_io_worker", None)
        if (self.worker and self.worker.isRunning()) or (io_worker and io_worker.isRunning()):
            self.reject(); event.ignore()
        else:
            super().closeEvent(event)

    def diagnose(self, checked=False, *, paths=None, query=None, force=False):
        paths = tuple(paths if paths is not None else self.selected_paths())
        if not paths:
            QMessageBox.information(self, "没有目标", "当前筛选没有可诊断的失败项。"); return
        query = query if query is not None else self.applied_query
        from dataclasses import replace
        from core.analysis_failures import FailureStore
        groups = FailureStore(self.service.db).query(replace(query, scope_paths=tuple(paths)), groups_only=True)
        route = self.service.ai.resolve_task_route("error_diagnosis")
        cache_hint = "本次忽略缓存" if force else "缓存命中不请求"
        if QMessageBox.question(self, "AI诊断预览", f"共 {len(groups)} 个错误组，最多 {len(groups)} 次请求（{cache_hint}）。\n连接：{route.get('provider_id')} / {route.get('model')}；串行执行。\n仅发送脱敏错误和技术上下文，是否开始？") != QMessageBox.Yes:
            return
        from gui.workers.io_worker import run_io_job
        self.diagnose_btn.setEnabled(False)
        self.retry_btn.setEnabled(False)
        self.cancel_diagnosis_btn.setEnabled(True)
        self.diagnosis_cancel.clear()
        self.summary.setText(f"正在诊断：已完成 0 / {len(groups)} 组")
        def done(result):
            self.diagnose_btn.setEnabled(True)
            self.retry_btn.setEnabled(True)
            self.cancel_diagnosis_btn.setEnabled(False)
            self.cancel_diagnosis_btn.setText("取消诊断")
            self.summary.setText(f"诊断结束：成功 {result.get('succeeded', 0)} 组，失败 {result.get('failed', 0)} 组，未执行/取消 {result.get('cancelled', 0)} 组")
            self._show_diagnosis_results(result, query, tuple(paths))
        def failed(error):
            self.diagnose_btn.setEnabled(True); self.retry_btn.setEnabled(True); self.cancel_diagnosis_btn.setEnabled(False)
            self.cancel_diagnosis_btn.setText("取消诊断")
            self.summary.setText("诊断已结束，发生错误：" + str(error))
            QMessageBox.warning(self, "诊断失败", str(error))
        self.cancel_diagnosis_btn.setText("取消诊断")
        run_io_job(self, fn=lambda: self.service.diagnose_analysis_failures(query, paths=paths, force=force, cancel_event=self.diagnosis_cancel, on_progress=self.diagnosis_progress.emit), on_ok=done, on_fail=failed, busy_message="正在诊断失败原因…")

    def _show_diagnosis_results(self, result, query, paths):
        from dataclasses import replace
        from core.analysis_failures import FailureStore
        dialog = QDialog(self); dialog.setWindowTitle("AI建议（不覆盖本地分类）"); dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"成功 {result.get('succeeded', 0)} 组 · 失败 {result.get('failed', 0)} 组 · 未执行/取消 {result.get('cancelled', 0)} 组"))
        content = QWidget(); cards = QVBoxLayout(content)
        for index, group in enumerate(result.get("groups", []), 1):
            card = QGroupBox(f"第 {index} 组 · {CATEGORIES.get(group.get('category'), '未知')} · {group.get('count')}条")
            card_layout = QVBoxLayout(card)
            diagnosis = group["diagnosis"]
            text = (f"阶段：{STAGES.get(group.get('stage'), group.get('stage') or '未知')} · 连接：{group.get('provider_id') or '未知'} / {group.get('model') or '未知'} · HTTP {group.get('status_code') or '—'}\n"
                    f"可能原因：{diagnosis.get('cause') or '未知'}\n依据：{diagnosis.get('evidence') or '—'}\n"
                    f"确定程度：{diagnosis.get('confidence', '未知')}（AI建议，需结合实际配置核实）\n建议：{'；'.join(diagnosis.get('actions') or []) or '—'}")
            label = QLabel(text); label.setWordWrap(True); label.setTextFormat(Qt.PlainText); label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card_layout.addWidget(label)
            suggestion = diagnosis.get("retry_options")
            if suggestion and not group.get("failed"):
                targets = FailureStore(self.service.db).query(replace(query, scope_paths=tuple(paths), fingerprints=(group["fingerprint"],)), paths_only=True)
                apply = QPushButton(f"应用第 {index} 组建议到重试设置（{len(targets)}条）")
                apply.clicked.connect(lambda checked=False, opts=suggestion, frozen=tuple(targets): self._apply_diagnosis(dialog, frozen, opts))
                card_layout.addWidget(apply)
            cards.addWidget(card)
        cards.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(content); layout.addWidget(scroll)
        refresh = QPushButton("重新诊断本次目标（忽略缓存）")
        refresh.clicked.connect(lambda: self._restart_diagnosis(dialog, query, tuple(paths)))
        layout.addWidget(refresh)
        failed_fingerprints = tuple(group["fingerprint"] for group in result.get("groups", []) if group.get("failed"))
        if failed_fingerprints:
            failed_paths = tuple(FailureStore(self.service.db).query(replace(query, scope_paths=tuple(paths), fingerprints=failed_fingerprints), paths_only=True))
            retry_failed = QPushButton(f"仅重试诊断失败组（{len(failed_fingerprints)}组）")
            retry_failed.clicked.connect(lambda: self._restart_diagnosis(dialog, query, failed_paths))
            layout.addWidget(retry_failed)
        close = QDialogButtonBox(QDialogButtonBox.Close); close.rejected.connect(dialog.reject); layout.addWidget(close)
        dialog.exec()

    def _restart_diagnosis(self, dialog, query, paths):
        dialog.accept()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.diagnose(paths=paths, query=query, force=True))

    def _apply_diagnosis(self, dialog, paths, suggestion):
        try:
            validate_manual_options(suggestion, self.service.settings)
        except ValueError as exc:
            QMessageBox.warning(dialog, "AI建议无效", str(exc))
            return
        # 先退出诊断模态窗口，使重试期间的进度和取消按钮可交互。
        dialog.accept()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.retry(paths=paths, suggestion=suggestion))


def show_recovery_detail(service, path, parent):
    detail = service.get_recovery_detail(path)
    dialog = QDialog(parent)
    dialog.setWindowTitle("分析失败详情与历史")
    dialog.resize(1000, 600)
    layout = QVBoxLayout(dialog)
    if not detail:
        layout.addWidget(QLabel("此视频没有分析失败记录。"))
    else:
        info = detail["detail"]
        names = {p["id"]: p.get("display_name") or p["id"] for p in list_providers(service.settings)}
        error = info.get("last_error", {})
        success = info.get("successful_route", {})
        local = detail.get("failure") or classify(error)
        ever400 = bool(local.get("ever400", info.get("ever400", error.get("status_code") == 400)))
        summary = QPlainTextEdit()
        summary.setObjectName("failure_details")
        summary.setReadOnly(True)
        summary.setMaximumHeight(220)
        summary.setPlainText(
            failure_state_label(detail["state"]) +
            f"\n发生阶段：{STAGES.get(local.get('stage'), '未知阶段')}；原因分类：{CATEGORIES.get(local.get('category'), '未知错误')}；曾遇到400：{'是' if ever400 else '否'}"
            f"\n本地判断依据：{local.get('evidence') or '无现存依据'}\n处理建议：{local.get('suggestion') or '—'}"
            f"\n恢复请求：{info.get('used', 0)} / {info.get('budget', '—')}；更新时间：{detail['updated_at']}"
            f"\n最近错误：HTTP {error.get('status_code', '—')} / {error.get('error_code') or '无错误码'}；{error.get('message') or '—'}"
            f"\n成功供应商／模型：{names.get(success.get('provider_id'), success.get('provider_id')) or '—'} / {success.get('model') or '—'}"
            f"\n历史：{len(detail['rounds'])}轮；失效兜底：{'；'.join(info.get('invalid_routes', [])) or '无'}")
        layout.addWidget(summary)
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["时间", "任务", "供应商／模型", "HTTP", "错误码", "结果／原因", "请求标识"])
        table.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate((155, 110, 175, 55, 95, 240, 130)):
            table.setColumnWidth(column, width)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for attempt in detail["attempts"]:
            row = table.rowCount()
            table.insertRow(row)
            item = attempt["detail"]
            values = [attempt["created_at"], TASK_NAMES.get(item.get("task_key"), item.get("task_key")), f"{names.get(item.get('provider_id'), item.get('provider_id'))} / {item.get('model')}", item.get("status_code"), item.get("error_code"), "成功" if item.get("success") else item.get("message"), item.get("request_id")]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value or "")); cell.setToolTip(str(value or ""))
                table.setItem(row, col, cell)
        layout.addWidget(table)
        layout.addWidget(QLabel("选中历史记录可在下方阅读和复制全文："))
        history_text = QPlainTextEdit(); history_text.setReadOnly(True)
        history_text.setObjectName("history_full_text"); history_text.setMaximumHeight(150)
        layout.addWidget(history_text)
        def read_attempt(row, column, previous_row, previous_column):
            if not 0 <= row < len(detail["attempts"]):
                history_text.clear(); return
            attempt = detail["attempts"][row]
            item = attempt["detail"]
            history_text.setPlainText(
                f"时间：{attempt['created_at']}\n任务：{TASK_NAMES.get(item.get('task_key'), item.get('task_key') or '未知')}"
                f"\n供应商／模型：{names.get(item.get('provider_id'), item.get('provider_id')) or '未知'} / {item.get('model') or '未知'}"
                f"\nHTTP：{item.get('status_code') or '—'}；错误码：{item.get('error_code') or '—'}"
                f"\n结果：{'成功' if item.get('success') else '失败'}\n完整错误：{item.get('message') or '—'}"
                f"\n请求标识：{item.get('request_id') or '—'}")
        table.currentCellChanged.connect(read_attempt)
        if table.rowCount(): table.setCurrentCell(0, 0)
        layout.addWidget(QLabel("显示最近50轮、200次尝试；完整历史保存在数据库中。"))
        settings_button = QPushButton("设置此视频的专属恢复策略（不发起请求）")
        settings_button.clicked.connect(lambda: VideoRecoverySettingsDialog(service, path, dialog).exec())
        layout.addWidget(settings_button)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.button(QDialogButtonBox.Close).setText("关闭")
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def launch_manual_recovery(view):
    from gui.workers.analysis_worker import AnalysisWorker
    lock = getattr(view.service, "_analysis_run_lock", None)
    if lock and lock.locked():
        QMessageBox.information(view, "分析正在运行", "请等待当前分析结束，或取消当前任务后再重试。")
        return
    paths = view.service.recovery_target_paths(list(view._resolve_batch_snapshot().target_paths))
    if not paths:
        QMessageBox.information(view, "重试400失败项", "当前操作目标集中没有未恢复的400视频。")
        return
    dialog = ManualRecoveryDialog(view.service, paths, view)
    if dialog.exec() != QDialog.Accepted:
        return
    if hasattr(view, "set_ui_enabled"):
        view.set_ui_enabled(False)
    view.recovery_worker = AnalysisWorker(view.service, list(dialog.paths), force_reanalyze=True, recovery_options=dialog.options)
    if hasattr(view, "worker"):
        view.worker = view.recovery_worker
    if hasattr(view, "cancel_analysis_btn"):
        view.cancel_analysis_btn.setEnabled(True)
    view.recovery_cancel_btn.setEnabled(True)
    view.recovery_retry_btn.setEnabled(False)
    def finished(ok, message):
        if hasattr(view, "set_ui_enabled"):
            view.set_ui_enabled(True)
        view.recovery_retry_btn.setEnabled(True)
        view.recovery_cancel_btn.setEnabled(False)
        if hasattr(view, "model") and hasattr(view.model, "clear_temp_status_map"):
            view.model.clear_temp_status_map()
        if hasattr(view, "cancel_analysis_btn"):
            view.cancel_analysis_btn.setEnabled(False)
        if hasattr(view, "progress_bar"):
            view.progress_bar.setVisible(False)
            view.sub_progress_bar.setVisible(False)
        view.service.invalidate_video_memory_cache()
        view.load_data()
        QMessageBox.information(view, "400重试结束", message)
    view.recovery_worker.task_finished.connect(finished)
    view.recovery_worker.progress_updated.connect(lambda _percent, message: view.recovery_status_label.setText(message))
    if hasattr(view, "on_analysis_snapshot"):
        view.recovery_worker.snapshot_updated.connect(view.on_analysis_snapshot)
    else:
        def update_rows(snapshot):
            mapping = {**view.model.temp_status_by_path, **snapshot.get("recovery_rows", {})}
            view.model.set_temp_status_map(mapping)
        view.recovery_worker.snapshot_updated.connect(update_rows)
    view.recovery_worker.start()


def launch_failure_center(view):
    try:
        paths = list(view._resolve_batch_snapshot().target_paths)
    except Exception:
        QMessageBox.warning(view, "无法获取操作范围", "当前选中项或筛选范围读取失败，请刷新列表后重试。未打开全库失败项。")
        return
    AnalysisFailureCenterDialog(view.service, paths=paths, parent=view).exec()


def add_recovery_toolbar(view, toolbar, layout):
    view.failure_center_btn = QPushButton("分析失败处理")
    view.failure_center_btn.setToolTip("查看全部分析失败的原因、历史并进行手动重试或AI诊断。")
    view.failure_center_btn.clicked.connect(lambda: launch_failure_center(view))
    toolbar.addWidget(view.failure_center_btn)
    view.recovery_retry_btn = QPushButton("重试400失败项")
    view.recovery_retry_btn.setToolTip("有选中时处理选中项；无选中时处理全部筛选结果中的400失败项。")
    view.recovery_retry_btn.clicked.connect(lambda: launch_manual_recovery(view))
    toolbar.addWidget(view.recovery_retry_btn)
    view.recovery_cancel_btn = QPushButton("取消400重试")
    view.recovery_cancel_btn.setEnabled(False)
    view.recovery_cancel_btn.clicked.connect(lambda: view.recovery_worker.request_cancel())
    toolbar.addWidget(view.recovery_cancel_btn)
    view.recovery_status_label = QLabel("")
    view.recovery_status_label.setTextFormat(Qt.PlainText)
    view.recovery_status_label.setWordWrap(True)
    layout.addWidget(view.recovery_status_label)
