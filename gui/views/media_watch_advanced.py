"""素材监控高级设置；与独立工作区共享配置。"""
import copy
import os
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QComboBox, QFormLayout, QLineEdit,
    QCheckBox, QDoubleSpinBox, QPushButton, QScrollArea, QHBoxLayout, QMessageBox,
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

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._loading = False
        layout = QVBoxLayout(self)
        title = QLabel('素材监控 · 高级设置')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        hint = QLabel('日常目录管理和处理进度请前往左侧「素材监控」。以下设置支持全局默认与单任务覆盖。\n'
                      '完全重复的副本始终只入库和标记，不分析、不打标、不改名、不写 XMP。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.target = QComboBox()
        self.target.currentIndexChanged.connect(self._populate)
        layout.addWidget(self.target)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.form = QFormLayout(container)
        self.fields, self.inherit = {}, {}
        for key, label in LABELS.items():
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
            inherit = QCheckBox('沿用上级')
            inherit.toggled.connect(lambda checked, w=widget: w.setEnabled(not checked))
            self.inherit[key] = inherit
            row = QHBoxLayout()
            row.addWidget(widget, 1)
            row.addWidget(inherit)
            self.form.addRow('' if isinstance(widget, QCheckBox) else label, row)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        save = QPushButton('保存监控设置')
        save.setObjectName('primary_button')
        save.clicked.connect(self._save)
        layout.addWidget(save)
        self.refresh()

    def refresh(self):
        selected = self.target.currentData()
        self.config = load_watch_config(self.service)
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItem('全局默认', '')
        for task in self.config['directories']:
            self.target.addItem(task['name'], task['id'])
        self.target.setCurrentIndex(max(0, self.target.findData(selected)))
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
            check.setVisible(task is not None)
            check.setChecked(task is not None and task.get(key) is None)
            widget.setEnabled(not check.isChecked() and (key != 'start_with_windows' or os.name == 'nt'))
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(value if value is not None else widget.minimum())
            else:
                widget.setText('; '.join(value) if isinstance(value, list) else str(value or ''))
        self._loading = False

    def _save(self):
        config = load_watch_config(self.service)
        task_id = self.target.currentData()
        target = next((task for task in config['directories'] if task['id'] == task_id), config)
        for key, widget in self.fields.items():
            if target is not config and self.inherit[key].isChecked():
                target[key] = None
            elif isinstance(widget, QComboBox):
                target[key] = widget.currentData()
            elif isinstance(widget, QCheckBox):
                target[key] = widget.isChecked()
            elif isinstance(widget, QDoubleSpinBox):
                value = widget.value()
                target[key] = None if DEFAULTS[key] is None and value < LIMITS[key][0] else value
            else:
                target[key] = widget.text().strip()
        try:
            save_watch_config(self.service, config)
        except Exception as exc:
            QMessageBox.warning(self, '保存监控设置失败', str(exc))
            return
        self.changed.emit()
        self.refresh()
