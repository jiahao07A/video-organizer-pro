"""素材监控高级设置；与独立工作区共享配置。"""
import copy
import os
import re

from PySide6.QtCore import Signal, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QComboBox, QFormLayout, QLineEdit,
    QCheckBox, QDoubleSpinBox, QPushButton, QScrollArea, QHBoxLayout, QMessageBox, QGroupBox,
)
from core.watch_config import DEFAULTS, LIMITS, normalize_watch_config
from core.media_watch import set_windows_startup, should_start_with_windows
from core.video_organizer_service import SettingsManager


def load_watch_config(service):
    return normalize_watch_config(service.settings.get('processing', {}).get('media_watch', {}))


def save_watch_config(service, config):
    config = normalize_watch_config(config)
    for values in [config] + config['directories']:
        if values.get('regex_find'):
            re.compile(values['regex_find'])
    settings = copy.deepcopy(service.settings)
    settings.setdefault('processing', {})['media_watch'] = config
    set_windows_startup(should_start_with_windows(config))
    try:
        SettingsManager.save_settings(settings, service.db)
    except Exception:
        set_windows_startup(should_start_with_windows(load_watch_config(service)))
        raise
    service.settings.setdefault('processing', {})['media_watch'] = config
    return config


LABELS = {
    'recursive': '递归扫描子目录', 'auto_analyze': '发现后自动分析', 'auto_rename': '分析后自动重命名',
    'rename_preset': '命名方案（留空继承命名设置）',
    'start_with_windows': 'Windows 登录时启动',
    'initial_scan_mode': '首次扫描默认范围', 'ask_initial_scan': '每次添加目录时询问首次扫描范围',
    'stability_seconds': '等待写入稳定（秒）', 'check_interval': '稳定性检查间隔（秒）',
    'rescan_interval': '周期补扫间隔（秒）', 'batch_size': '每批最多素材数',
    'max_workers': '后台分析并发上限（0＝跟随分析设置）',
    'request_interval': '请求间隔（秒，-1＝跟随分析设置）',
    'call_extra_attempts': '调用额外重试次数（-1＝跟随分析设置）',
    'item_max_attempts': '单条最多尝试（0＝跟随分析设置）',
    'batch_rerun_max_rounds': '批次补跑轮数（-1＝跟随分析设置）',
    'extensions': '视频扩展名', 'include_patterns': '包含文件规则',
    'exclude_patterns': '排除文件规则', 'excluded_dirs': '排除子目录（相对路径）',
    'rename_pattern': '自定义命名模板（留空跟随现有方案）',
    'regex_find': '文件名正则查找', 'regex_replace': '正则替换为',
    'rename_conflict': '自动重命名遇到冲突', 'xmp_mode': '分析后的 XMP 侧车',
    'notify_complete': '批次完成时通知', 'notify_failure': '批次失败时通知',
}
CHOICES = {
    'initial_scan_mode': [('处理全部既有视频', 'all'), ('仅处理后续新增', 'new')],
    'rename_conflict': [('进入待处理，不改名', 'review'), ('安全追加序号', 'suffix')],
    'xmp_mode': [('跟随现有 XMP 设置', 'inherit'), ('开启', 'on'), ('关闭', 'off')],
}


class MediaWatchSettingsView(QWidget):
    changed = Signal()

    def __init__(self, service, parent=None, *, mode="task", task_id=""):
        super().__init__(parent)
        self.service = service
        self.mode = mode
        self.task_id = str(task_id or "")
        self._loading = False
        layout = QVBoxLayout(self)
        title = QLabel('监控全局默认' if mode == "global" else '当前监控任务规则')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        hint = QLabel(('控制所有未单独覆盖的监控任务；修改后对下次监控批次生效。' if mode == "global" else '此处只编辑当前目录任务；未覆盖的项目继续沿用监控全局默认。') + '\n'
                      '完全重复的副本始终只入库和标记，不分析、不打标、不改名、不写 XMP。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.target = QComboBox()
        self.target.currentIndexChanged.connect(self._populate)
        layout.addWidget(self.target)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        cards = QVBoxLayout(container)
        self.form = QFormLayout()
        self._forms = {}
        groups = {
            "自动处理": {"recursive", "auto_analyze", "auto_rename", "start_with_windows"},
            "扫描与文件稳定": {"initial_scan_mode", "ask_initial_scan", "stability_seconds", "check_interval", "rescan_interval", "batch_size"},
            "文件筛选": {"extensions", "include_patterns", "exclude_patterns", "excluded_dirs"},
            "命名与 XMP": {"rename_preset", "rename_pattern", "regex_find", "regex_replace", "rename_conflict", "xmp_mode"},
            "分析请求与费用（高级）": {"max_workers", "request_interval", "call_extra_attempts", "item_max_attempts", "batch_rerun_max_rounds"},
            "通知": {"notify_complete", "notify_failure"},
        }
        self.cards = {}
        for title, keys in groups.items():
            group = QGroupBox(title)
            body = QWidget()
            form = QFormLayout(body)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            outer = QVBoxLayout(group)
            outer.addWidget(body)
            if "高级" in title:
                group.setCheckable(True)
                group.setChecked(False)
                body.hide()
                group.toggled.connect(body.setVisible)
            cards.addWidget(group)
            self.cards[title] = group
            for key in keys:
                self._forms[key] = form
            if mode == "global":
                button = QPushButton("恢复本卡片默认")
                button.clicked.connect(lambda _=False, fields=keys: self._reset_fields(fields))
                outer.addWidget(button)
        cards.addStretch()
        self.fields, self.inherit = {}, {}
        for key, label in LABELS.items():
            if mode == "task" and key in {"start_with_windows", "ask_initial_scan"}:
                continue
            default = DEFAULTS[key]
            if key in CHOICES:
                widget = QComboBox()
                for text, value in CHOICES[key]:
                    widget.addItem(text, value)
            elif key in LIMITS:
                widget = QDoubleSpinBox()
                widget.setDecimals(1 if key in ('stability_seconds', 'check_interval', 'rescan_interval', 'request_interval') else 0)
                minimum, maximum = LIMITS[key]
                widget.setRange(minimum if default is not None else (-1 if minimum == 0 else 0), maximum)
            elif isinstance(default, bool):
                widget = QCheckBox(label)
            else:
                widget = QLineEdit()
                if isinstance(default, list):
                    widget.setPlaceholderText('多项用分号分隔；文件规则支持 * 和 ?')
            self.fields[key] = widget
            inherit = QCheckBox('继承全局')
            inherit.toggled.connect(lambda checked, w=widget: w.setEnabled(not checked))
            self.inherit[key] = inherit
            row = QHBoxLayout()
            row.addWidget(widget, 1)
            row.addWidget(inherit)
            self._forms[key].addRow('' if isinstance(widget, QCheckBox) else label, row)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self.save_status = QLabel("自动保存；未覆盖的项目持续继承全局。")
        self.save_status.setWordWrap(True)
        layout.addWidget(self.save_status)
        self.save_button = QPushButton("重试保存")
        self.save_button.hide()
        layout.addWidget(self.save_button)
        self.target.hide()
        self.refresh()
        if mode == "task":
            from gui.services.settings_controller import SettingsController
            self.controller = SettingsController(service, self)
            self.controller.pending.connect(lambda: self.save_status.setText("任务规则待保存…"))
            self.controller.save_failed.connect(self._failed)
            self.controller.saved.connect(self._saved)
            self.save_button.clicked.connect(self.controller.flush_now)
        for control in self.fields.values():
            self._connect_change(control)
            control.installEventFilter(self)
        for check in self.inherit.values():
            check.toggled.connect(lambda checked, key=next(k for k, value in self.inherit.items() if value is check): self._inherit_changed(key, checked))

    def _inherit_changed(self, key, checked):
        if self._loading:
            return
        if checked:
            self._loading = True
            self._set_value(key, self.fields[key], load_watch_config(self.service)[key])
            self._loading = False
        self._changed()

    def _failed(self, message):
        self.save_status.setText(message)
        self.save_button.show()

    def _saved(self, message):
        self.save_status.setText(message)
        self.save_button.hide()
        self.changed.emit()

    def eventFilter(self, obj, event):
        if self.mode == "task" and event.type() == QEvent.FocusOut and isinstance(obj, QLineEdit):
            self.controller.flush_now()
        return super().eventFilter(obj, event)

    def _reset_fields(self, fields):
        if QMessageBox.question(self, "恢复监控默认", "只恢复本卡片的全局默认；目录专属覆盖保留。",
                               QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._loading = True
        for key in fields:
            widget = self.fields.get(key)
            if widget is not None:
                self._set_value(key, widget, DEFAULTS[key])
        self._loading = False
        self.changed.emit()

    def _connect_change(self, widget):
        signal = None
        for name in ('valueChanged', 'currentIndexChanged', 'toggled', 'textChanged'):
            candidate = getattr(widget, name, None)
            if candidate is not None:
                signal = candidate
                break
        if signal is not None:
            signal.connect(self._changed)

    def _changed(self, *_):
        if self._loading:
            return
        if self.mode == "global":
            self.changed.emit()
        else:
            self.controller.schedule_save(self._collect_task)

    def refresh(self):
        selected = self.target.currentData()
        self.config = load_watch_config(self.service)
        self.target.blockSignals(True)
        self.target.clear()
        if self.mode == "global":
            self.target.addItem('全局默认', '')
        elif self.task_id:
            task = next((task for task in self.config['directories'] if task['id'] == self.task_id), None)
            if task is not None:
                self.target.addItem(task['name'], task['id'])
            else:
                self.target.addItem('监控目录不存在', self.task_id)
        else:
            self.target.addItem('全局默认', '')
            for task in self.config['directories']:
                self.target.addItem(task['name'], task['id'])
        self.target.setCurrentIndex(max(0, self.target.findData(self.task_id or selected)))
        self.target.blockSignals(False)
        self._populate()

    def _populate(self, *_):
        if not hasattr(self, 'config'):
            return
        task_id = self.target.currentData()
        task = next((task for task in self.config['directories'] if task['id'] == task_id), None)
        self._loading = True
        for key, widget in self.fields.items():
            value = self.config[key] if task is None or task.get(key) is None else task[key]
            check = self.inherit[key]
            check.setVisible(self.mode == "task" and task is not None)
            check.setChecked(task is not None and task.get(key) is None)
            widget.setEnabled(not check.isChecked() and (self.mode == 'global' or task is not None) and (key != 'start_with_windows' or os.name == 'nt'))
            self._set_value(key, widget, value)
        self._loading = False

    def _set_value(self, key, widget, value):
        if isinstance(widget, QComboBox):
            widget.setCurrentIndex(max(0, widget.findData(value)))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(value if value is not None else widget.minimum())
        else:
            widget.setText("; ".join(value) if isinstance(value, list) else str(value or ""))

    def collect_global_config(self):
        config = load_watch_config(self.service)
        for key, widget in self.fields.items():
            config[key] = self._read_field(key, widget)
        return config

    def _read_field(self, key, widget):
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QDoubleSpinBox):
            value = widget.value()
            return None if DEFAULTS[key] is None and value < LIMITS[key][0] else value
        value = widget.text().strip()
        if isinstance(DEFAULTS[key], list):
            return [item.strip() for item in value.replace('\n', ';').split(';') if item.strip()]
        return value

    def _collect_task(self):
        config = load_watch_config(self.service)
        task_id = self.target.currentData()
        target = next((task for task in config["directories"] if task["id"] == task_id), None)
        if target is None:
            raise ValueError("监控任务不存在")
        for key, widget in self.fields.items():
            target[key] = None if self.inherit[key].isChecked() else self._read_field(key, widget)
        for values in (config, target):
            if values.get("regex_find"):
                re.compile(values["regex_find"])
        self.service.settings.setdefault("processing", {})["media_watch"] = normalize_watch_config(config)
        return True

    def _save(self):
        if self.mode == "global":
            self.changed.emit()
        else:
            self.controller.schedule_save(self._collect_task)
            return self.controller.flush_now()
