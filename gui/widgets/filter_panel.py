# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QDateEdit, QCheckBox, QPushButton, QComboBox,
    QLineEdit,
)
from PySide6.QtCore import Qt, Signal, QDate
from .checkable_combo_box import CheckableComboBox
from gui.styles import normalize_theme, get_theme_colors


class FilterPanel(QFrame):
    """高级筛选面板 — 跟随全局 light/dark 主题；工作台与素材库共用。"""
    filterChanged = Signal(dict)

    def __init__(self, settings, parent=None, service=None):
        super().__init__(parent)
        self.settings = settings
        self.service = service
        self.setObjectName("FilterPanel")
        self.setup_ui()
        self.apply_theme()

    def setup_ui(self):
        self.setFrameShape(QFrame.StyledPanel)
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.cat_combo = CheckableComboBox()
        self.cat_combo.setPlaceholderText("筛选分类...")
        for cat in self.settings.get("categories", []):
            self.cat_combo.add_item(cat)
        self.cat_combo.itemsChanged.connect(self.emit_filter)

        self.tag_combo = CheckableComboBox()
        self.tag_combo.setPlaceholderText("筛选标签...")
        all_tags = set()
        for tags in self.settings.get("tag_dimensions", {}).values():
            all_tags.update(tags)
        for tag in sorted(list(all_tags)):
            self.tag_combo.add_item(tag)
        self.tag_combo.itemsChanged.connect(self.emit_filter)

        self.status_combo = CheckableComboBox()
        self.status_combo.setPlaceholderText("分析状态...")
        for s in ["未分析", "已分析", "未命名", "已重命名"]:
            self.status_combo.add_item(s)
        self.status_combo.itemsChanged.connect(self.emit_filter)

        self.tag_mode_combo = QComboBox()
        self.tag_mode_combo.addItem("标签：满足任一", "any")
        self.tag_mode_combo.addItem("标签：同时全部", "all")
        self.tag_mode_combo.currentIndexChanged.connect(self.emit_filter)

        self.summary_combo = QComboBox()
        self.summary_combo.addItem("摘要：不限", "any")
        self.summary_combo.addItem("摘要：为空", "empty")
        self.summary_combo.addItem("摘要：非空", "nonempty")
        self.summary_combo.currentIndexChanged.connect(self.emit_filter)

        self.date_start = QDateEdit()
        self.date_start.setDisplayFormat("yyyy-MM-dd")
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addMonths(-1))
        self.date_start.setEnabled(True)
        self.date_start.dateChanged.connect(self._on_date_value_changed)

        self.date_end = QDateEdit()
        self.date_end.setDisplayFormat("yyyy-MM-dd")
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate())
        self.date_end.setEnabled(True)
        self.date_end.dateChanged.connect(self._on_date_value_changed)

        self.dup_cb = QCheckBox("仅显示重复项")
        self.dup_cb.setToolTip("全库中非空文件内容哈希相同的素材；未计算哈希或仅单帧相似的素材不认定重复。")
        self.dup_cb.stateChanged.connect(self.emit_filter)

        # 用 toggled(bool)，避免 stateChanged 的 int/CheckState 比较失败导致永远不启用
        self.date_check = QCheckBox("启用日期筛选")
        self.date_check.setToolTip("勾选后按下方起止日期过滤；也可直接改日期（会自动勾选）")
        self.date_check.toggled.connect(self._on_date_filter_toggled)

        layout.addWidget(QLabel("分类:"), 0, 0)
        layout.addWidget(self.cat_combo, 0, 1)
        layout.addWidget(QLabel("标签:"), 0, 2)
        layout.addWidget(self.tag_combo, 0, 3)
        layout.addWidget(self.dup_cb, 0, 4)

        # 基调通过标签（含氛围）筛选，不再提供独立情绪维
        layout.addWidget(QLabel("状态:"), 1, 0)
        layout.addWidget(self.status_combo, 1, 1)
        layout.addWidget(self.tag_mode_combo, 1, 2)
        layout.addWidget(self.summary_combo, 1, 3)

        layout.addWidget(self.date_check, 2, 0)
        layout.addWidget(self.date_start, 2, 1)
        layout.addWidget(QLabel("至"), 2, 2, alignment=Qt.AlignCenter)
        layout.addWidget(self.date_end, 2, 3)

        reset_btn = QPushButton("重置")
        self.recovery_combo = QComboBox()
        for text, value in (("400：不限", ""), ("当前400未恢复", "unresolved"), ("重试耗尽", "exhausted"), ("曾遇到400", "ever")):
            self.recovery_combo.addItem(text, value)
        self.recovery_combo.currentIndexChanged.connect(self.emit_filter)
        layout.addWidget(self.recovery_combo, 3, 1, 1, 2)
        reset_btn.clicked.connect(self.reset_filter)
        layout.addWidget(reset_btn, 3, 4)
        self.source_combo = QComboBox()
        for label, value in [('来源：全部',''), ('监控入库','watch'), ('手动入库','manual'), ('历史来源未知','unknown')]:
            self.source_combo.addItem(label, value)
        self.watch_task_combo = QComboBox()
        self.watch_task_combo.addItem('全部监控任务', '')
        if self.service:
            from core.watch_config import normalize_watch_config
            for task in normalize_watch_config(self.settings.get('processing', {}).get('media_watch', {}))['directories']:
                self.watch_task_combo.addItem(task['name'], task['id'])
        self.review_combo = QComboBox()
        for label, value in [('核对：全部',''), ('未核对','unreviewed'), ('已核对','reviewed')]:
            self.review_combo.addItem(label, value)
        self.duplicate_role_combo = QComboBox()
        for label, value in [('重复身份：全部',''), ('重复组原记录','original'), ('重复副本','copy')]:
            self.duplicate_role_combo.addItem(label, value)
        self.watch_stage_combo = QComboBox()
        self.watch_stage_combo.addItem('全部监控阶段', '')
        from gui.views.media_watch_workspace import STAGES
        for value, label in STAGES.items():
            self.watch_stage_combo.addItem(label, value)
        for column, widget in enumerate((self.source_combo,self.watch_task_combo,self.review_combo,self.duplicate_role_combo,self.watch_stage_combo)):
            widget.currentIndexChanged.connect(self.emit_filter)
            layout.addWidget(widget, 4, column)
        self.batch_input = QLineEdit()
        self.batch_input.setPlaceholderText('按完整监控批次编号筛选')
        self.batch_input.textChanged.connect(self.emit_filter)
        layout.addWidget(self.batch_input, 5, 0, 1, 5)

    def refresh_watch_tasks(self):
        from core.watch_config import normalize_watch_config
        selected = self.watch_task_combo.currentData()
        self.watch_task_combo.blockSignals(True)
        self.watch_task_combo.clear()
        self.watch_task_combo.addItem('全部监控任务', '')
        tasks = normalize_watch_config(self.service.settings.get('processing', {}).get('media_watch', {}))['directories'] if self.service else []
        for task in tasks:
            self.watch_task_combo.addItem(task['name'], task['id'])
        if selected and self.watch_task_combo.findData(selected) < 0:
            self.watch_task_combo.addItem('已移除任务 ' + selected[:8], selected)
        self.watch_task_combo.setCurrentIndex(max(0, self.watch_task_combo.findData(selected)))
        self.watch_task_combo.blockSignals(False)

    def apply_theme(self, theme=None):
        if theme is None:
            theme = self.settings.get("ui_preferences", {}).get("theme", "dark")
        theme = normalize_theme(theme)
        c = get_theme_colors(theme)
        self.setStyleSheet(f"""
            #FilterPanel {{
                background-color: {c["filter_bg"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
                color: {c["text"]};
            }}
            #FilterPanel QLabel {{
                color: {c["text"]};
                background: transparent;
            }}
            #FilterPanel QCheckBox {{
                color: {c["text"]};
                background: transparent;
            }}
            #FilterPanel QLineEdit, #FilterPanel QComboBox, #FilterPanel QDateEdit {{
                background-color: {c["item_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
            }}
            #FilterPanel QDateEdit:disabled {{
                color: {c["text"]};
                background-color: {c["item_bg"]};
            }}
            #FilterPanel QPushButton {{
                background-color: {c["item_bg"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 4px;
            }}
        """)

    def _on_date_filter_toggled(self, checked: bool):
        """启用/关闭日期条件；日期控件始终可点，避免「点不动」。"""
        self.emit_filter()

    def _on_date_value_changed(self, *_args):
        """用户改日期时自动打开「启用日期筛选」。"""
        if not self.date_check.isChecked():
            self.date_check.blockSignals(True)
            self.date_check.setChecked(True)
            self.date_check.blockSignals(False)
        self.emit_filter()

    def reset_filter(self):
        for name in ('source_combo','watch_task_combo','review_combo','duplicate_role_combo','watch_stage_combo'):
            if hasattr(self, name):
                getattr(self, name).setCurrentIndex(0)
        if hasattr(self, 'batch_input'):
            self.batch_input.clear()
        self.cat_combo.clear_items()
        for cat in self.settings.get("categories", []):
            self.cat_combo.add_item(cat)
        self.tag_combo.clear_items()
        all_tags = set()
        for tags in self.settings.get("tag_dimensions", {}).values():
            all_tags.update(tags)
        for tag in sorted(list(all_tags)):
            self.tag_combo.add_item(tag)
        self.status_combo.clear_items()
        for s in ["未分析", "已分析", "未命名", "已重命名"]:
            self.status_combo.add_item(s)
        self.tag_mode_combo.setCurrentIndex(0)
        self.summary_combo.setCurrentIndex(0)
        self.recovery_combo.setCurrentIndex(0)
        self.dup_cb.setChecked(False)
        self.date_check.blockSignals(True)
        self.date_check.setChecked(False)
        self.date_check.blockSignals(False)
        self.date_start.blockSignals(True)
        self.date_end.blockSignals(True)
        self.date_start.setDate(QDate.currentDate().addMonths(-1))
        self.date_end.setDate(QDate.currentDate())
        self.date_start.blockSignals(False)
        self.date_end.blockSignals(False)
        self.emit_filter()

    def emit_filter(self):
        alias_map = {}
        if self.service is not None and getattr(self.service, "db", None) is not None:
            try:
                alias_map = self.service.db.get_synonyms() or {}
            except Exception:
                alias_map = {}
        params = {
            "categories": self.cat_combo.get_checked_items(),
            "tags": self.tag_combo.get_checked_items(),
            "analysis_statuses": self.status_combo.get_checked_items(),
            "tag_match_mode": self.tag_mode_combo.currentData() or "any",
            "summary_empty": self.summary_combo.currentData() or "any",
            "only_dup": self.dup_cb.isChecked(),
            "date_start": self.date_start.date() if self.date_check.isChecked() else None,
            "date_end": self.date_end.date() if self.date_check.isChecked() else None,
            "alias_map": alias_map,
            "recovery_filter": self.recovery_combo.currentData() or "",
            'source_kind': self.source_combo.currentData() if hasattr(self, 'source_combo') else '',
            'watch_task_id': self.watch_task_combo.currentData() if hasattr(self, 'watch_task_combo') else '',
            'review_state': self.review_combo.currentData() if hasattr(self, 'review_combo') else '',
            'duplicate_role': self.duplicate_role_combo.currentData() if hasattr(self, 'duplicate_role_combo') else '',
            'watch_stage': self.watch_stage_combo.currentData() if hasattr(self, 'watch_stage_combo') else '',
            'watch_batch_id': self.batch_input.text().strip() if hasattr(self, 'batch_input') else '',
        }
        self.filterChanged.emit(params)
