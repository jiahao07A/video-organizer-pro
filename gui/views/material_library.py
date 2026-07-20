# -*- coding: utf-8 -*-
"""素材库：全部已入库视频；支持筛选与累加到工作范围。"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QSplitter, QStackedWidget, QTableView, QListView,
    QHeaderView, QAbstractItemView, QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from ..widgets.filter_panel import FilterPanel
from ..widgets.detail_panel import DetailPanel
from ..widgets.delegates import CardDelegate, StatusDelegate, ThumbnailDelegate
from ..models.video_table import VideoTableModel
from ..models.proxy_model import AdvancedSortFilterProxyModel
from core.video_organizer_service import VideoOrganizerService
from gui.styles import normalize_theme


class MaterialLibraryView(QWidget):
    """素材库：呈现全部入库素材，可累加到工作范围。"""
    status_message = Signal(str)
    work_scope_changed = Signal()  # 通知工作台刷新

    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = service.settings
        self.setup_ui()
        self.apply_default_view()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        toolbar = QHBoxLayout()
        title = QLabel("素材库 — 全部已入库视频")
        title.setStyleSheet("font-weight: bold;")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索视频...")
        self.search_input.setFixedWidth(220)
        self.search_input.textChanged.connect(self.filter_text_changed)

        self.filter_btn = QPushButton("高级筛选")
        self.filter_btn.setCheckable(True)
        self.filter_btn.clicked.connect(self.toggle_filter_panel)

        self.view_switch_btn = QPushButton("切换视图")
        self.view_switch_btn.setCheckable(True)
        self.view_switch_btn.clicked.connect(self.toggle_view_mode)

        self.add_scope_btn = QPushButton("累加到工作范围")
        self.add_scope_btn.setObjectName("primary_button")
        self.add_scope_btn.setStyleSheet("background-color: #3d5afe; color: white; font-weight: bold;")
        self.add_scope_btn.setToolTip("将列表勾选的视频路径追加进工作范围")
        self.add_scope_btn.clicked.connect(self.accumulate_selected_to_scope)

        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.filter_btn)
        toolbar.addWidget(self.view_switch_btn)
        toolbar.addWidget(self.add_scope_btn)
        layout.addLayout(toolbar)

        self.filter_panel = FilterPanel(self.settings)
        self.filter_panel.setVisible(False)
        self.filter_panel.filterChanged.connect(self.apply_advanced_filter)
        layout.addWidget(self.filter_panel)

        self.splitter = QSplitter(Qt.Horizontal)
        self.view_stack = QStackedWidget()

        self.table_view = QTableView()
        self.model = VideoTableModel()
        self.proxy_model = AdvancedSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterKeyColumn(-1)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.table_view.setModel(self.proxy_model)
        self.table_view.setSortingEnabled(True)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.verticalHeader().setDefaultSectionSize(80)
        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_view.setColumnWidth(0, 50)
        self.table_view.setColumnWidth(1, 140)
        self.table_view.setItemDelegateForColumn(1, ThumbnailDelegate(self.table_view))
        self.table_view.setItemDelegateForColumn(3, StatusDelegate(self.table_view))
        self.table_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.view_stack.addWidget(self.table_view)

        self.card_view = QListView()
        self.card_view.setModel(self.proxy_model)
        self.card_view.setViewMode(QListView.IconMode)
        self.card_view.setResizeMode(QListView.Adjust)
        self.card_view.setMovement(QListView.Static)
        self.card_view.setSpacing(10)
        self.card_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.card_view.setItemDelegate(CardDelegate(self.card_view))
        self.card_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.view_stack.addWidget(self.card_view)

        self.splitter.addWidget(self.view_stack)
        self.detail_panel = DetailPanel(self.service)
        self.detail_panel.setMinimumWidth(280)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        expanded = self.settings.get("ui_preferences", {}).get("detail_panel_expanded", True)
        if not expanded:
            self.detail_panel.setVisible(False)

    def apply_default_view(self):
        mode = self.settings.get("ui_preferences", {}).get("default_view", "list")
        if mode == "card":
            self.view_switch_btn.setChecked(True)
            self.view_stack.setCurrentWidget(self.card_view)
            self.view_switch_btn.setText("切换列表")
        else:
            self.view_switch_btn.setChecked(False)
            self.view_stack.setCurrentIndex(0)
            self.view_switch_btn.setText("切换视图")

    def apply_theme(self, theme=None):
        if theme is None:
            theme = self.settings.get("ui_preferences", {}).get("theme", "dark")
        theme = normalize_theme(theme)
        if hasattr(self.filter_panel, "apply_theme"):
            self.filter_panel.apply_theme(theme)

    def toggle_filter_panel(self):
        self.filter_panel.setVisible(self.filter_btn.isChecked())

    def toggle_view_mode(self):
        if self.view_switch_btn.isChecked():
            self.view_stack.setCurrentWidget(self.card_view)
            self.view_switch_btn.setText("切换列表")
        else:
            self.view_stack.setCurrentIndex(0)
            self.view_switch_btn.setText("切换视图")

    def filter_text_changed(self, text):
        self.proxy_model.set_filter_params({"text": text})

    def apply_advanced_filter(self, params):
        self.proxy_model.set_filter_params(params)

    def load_data(self):
        videos = self.service.get_all_videos()
        self.model.update_data(videos)
        self.status_label.setText(f"素材库共 {len(videos)} 条")
        self.status_message.emit(self.status_label.text())

    def on_selection_changed(self, selected, deselected):
        if self.view_stack.currentWidget() == self.card_view:
            indexes = self.card_view.selectionModel().selectedIndexes()
        else:
            indexes = self.table_view.selectionModel().selectedRows()
        if not indexes:
            return
        rows = sorted(list(set(idx.row() for idx in indexes)))
        if len(rows) > 1:
            selected_videos = []
            for row in rows:
                proxy_idx = self.proxy_model.index(row, 0)
                source_idx = self.proxy_model.mapToSource(proxy_idx)
                selected_videos.append(self.model.videos[source_idx.row()])
            self.detail_panel.load_video_data(selected_videos)
        else:
            proxy_idx = self.proxy_model.index(rows[0], 0)
            source_idx = self.proxy_model.mapToSource(proxy_idx)
            self.detail_panel.load_video_data(self.model.videos[source_idx.row()])

    def _selected_video_paths(self):
        """勾选框优先；否则用当前行选中。"""
        checked = list(self.model.checked_items)
        if checked:
            return checked
        if self.view_stack.currentWidget() == self.card_view:
            indexes = self.card_view.selectionModel().selectedIndexes()
        else:
            indexes = self.table_view.selectionModel().selectedRows()
        paths = []
        for idx in indexes:
            src = self.proxy_model.mapToSource(self.proxy_model.index(idx.row(), 0))
            v = self.model.videos[src.row()]
            if v.get("path"):
                paths.append(v["path"])
        return list(dict.fromkeys(paths))

    def accumulate_selected_to_scope(self):
        paths = self._selected_video_paths()
        if not paths:
            QMessageBox.warning(self, "提示", "请先勾选或选中要累加的视频。")
            return
        result = self.service.append_work_scope(paths, scan=True)
        n = len(result.get("paths") or [])
        msg = f"已累加 {len(paths)} 条到工作范围，现共 {n} 项路径"
        self.status_label.setText(msg)
        self.status_message.emit(msg)
        self.work_scope_changed.emit()
        QMessageBox.information(self, "完成", msg)