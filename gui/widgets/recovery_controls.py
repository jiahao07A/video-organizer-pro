"""400恢复设置与手动恢复弹窗；普通用户无需编辑JSON。"""
import copy
import json

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QDialogButtonBox, QScrollArea,
    QMessageBox, QAbstractItemView, QGroupBox, QToolButton, QStackedWidget, QHeaderView, QSizePolicy, QLayout,
)
from core.analysis_recovery import DEFAULT_POLICY, effective_policy, validate_policy, RecoveryStore, STATE_LABELS
from core.model_providers import list_providers, find_provider
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
        hint = QLabel("每次手动重试开启新的有限轮次并跳过旧缓存。指定兜底用于上次失败的请求任务。以下预览以第一条视频为例；未覆盖字段分别继承各视频自身设置。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.mode = QComboBox()
        for text, value in (("按有效策略", "policy"), ("仅主API", "primary"), ("指定兜底API（直接请求）", "selected")):
            self.mode.addItem(text, value)
        layout.addWidget(self.mode)
        route_row = QHBoxLayout()
        self.provider = QComboBox()
        for p in list_providers(service.settings):
            self.provider.addItem(p.get("display_name") or p["id"], p["id"])
        self.model = ModelPicker(lambda: find_provider(service.settings, self.provider.currentData()))
        self.model.setPlaceholderText("选择备用模型，或手动输入")
        self.provider.currentIndexChanged.connect(self.model.refresh_connection)
        route_row.addWidget(self.provider)
        route_row.addWidget(self.model)
        layout.addLayout(route_row)
        self.mode.currentIndexChanged.connect(self._route_visibility)
        self._route_visibility()
        previous = service.get_recovery_detail(paths[0]) or {}
        task_key = previous.get("detail", {}).get("last_error", {}).get("task_key") or "video_classification"
        self.task_key = task_key
        base, sources = effective_policy(service.settings, task_key, video=RecoveryStore(service.db).override(paths[0]))
        self.editor = RecoveryPolicyEditor(service.settings, base=base, sources=sources)
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

    def _route_visibility(self):
        enabled = self.mode.currentData() == "selected"
        self.provider.setEnabled(enabled)
        self.model.setEnabled(enabled)

    def clear_video_overrides(self):
        if QMessageBox.question(self, "清除专属策略", f"将清除所选{len(self.paths)}条视频的专属恢复策略，是否继续？") != QMessageBox.Yes:
            return
        self.service.save_recovery_override(self.paths, {})
        self.editor.base, self.editor.sources = effective_policy(self.service.settings, self.task_key)
        self.editor.load_override({})
        self.editor._changed()

    def accept(self):
        try:
            override = validate_policy(self.editor.override(), self.service.settings)
            selected = None
            if self.mode.currentData() == "selected":
                selected = {"provider_id": self.provider.currentData(), "model": self.model.text().strip()}
                validate_policy({"fallbacks": [selected]}, self.service.settings)
            if self.save_video.isChecked():
                store = RecoveryStore(self.service.db)
                for path in self.paths:
                    self.service.save_recovery_override([path], {**store.override(path), **override})
            self.options = {"recovery_mode": self.mode.currentData(), "recovery_override": override, "selected_fallback": selected}
        except Exception as exc:
            QMessageBox.warning(self, "设置无效", str(exc))
            return
        super().accept()


def show_recovery_detail(service, path, parent):
    detail = service.get_recovery_detail(path)
    dialog = QDialog(parent)
    dialog.setWindowTitle("400恢复详情与历史")
    dialog.resize(1000, 600)
    layout = QVBoxLayout(dialog)
    if not detail:
        layout.addWidget(QLabel("此视频没有400恢复记录。"))
    else:
        info = detail["detail"]
        names = {p["id"]: p.get("display_name") or p["id"] for p in list_providers(service.settings)}
        error = info.get("last_error", {})
        success = info.get("successful_route", {})
        summary = QLabel(
            STATE_LABELS.get(detail["state"], detail["state"]) +
            f"\n恢复请求：{info.get('used', 0)} / {info.get('budget', '—')}；更新时间：{detail['updated_at']}"
            f"\n最近错误：HTTP {error.get('status_code', '—')} / {error.get('error_code') or '无错误码'}；{error.get('message') or '—'}"
            f"\n成功供应商／模型：{names.get(success.get('provider_id'), success.get('provider_id')) or '—'} / {success.get('model') or '—'}"
            f"\n历史：{len(detail['rounds'])}轮；失效兜底：{'；'.join(info.get('invalid_routes', [])) or '无'}")
        summary.setTextFormat(Qt.PlainText)
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(summary)
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["时间", "任务", "供应商／模型", "HTTP", "错误码", "结果／原因", "请求标识"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for attempt in detail["attempts"]:
            row = table.rowCount()
            table.insertRow(row)
            item = attempt["detail"]
            values = [attempt["created_at"], TASK_NAMES.get(item.get("task_key"), item.get("task_key")), f"{names.get(item.get('provider_id'), item.get('provider_id'))} / {item.get('model')}", item.get("status_code"), item.get("error_code"), "成功" if item.get("success") else item.get("message"), item.get("request_id")]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value or "")))
        layout.addWidget(table)
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
    view.recovery_worker = AnalysisWorker(view.service, paths, force_reanalyze=True, recovery_options=dialog.options)
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


def add_recovery_toolbar(view, toolbar, layout):
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
