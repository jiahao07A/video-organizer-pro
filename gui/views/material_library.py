# -*- coding: utf-8 -*-
"""素材库：全部已入库视频；支持筛选与累加到工作范围。"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QSplitter, QStackedWidget, QTableView, QListView,
    QHeaderView, QAbstractItemView, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QEvent, QObject
from PySide6.QtGui import QKeySequence, QShortcut
from ..widgets.filter_panel import FilterPanel
from ..widgets.detail_panel import DetailPanel
from ..widgets.delegates import CardDelegate, MaterialTagsColumnDelegate, StatusDelegate, ThumbnailDelegate
from ..models.video_table import (
    VideoTableModel,
    COL_FILENAME,
    COL_LIBRARY_ID,
    COL_LIST_NO,
    COL_STATUS,
    COL_TAGS,
    COL_THUMB,
)
from ..models.proxy_model import AdvancedSortFilterProxyModel
from core.video_organizer_service import VideoOrganizerService, SettingsManager
from gui.styles import normalize_theme


class _ClearSelectionOnEmptyClickFilter(QObject):
    """点空白区域清除选中。"""

    def __init__(self, view: QAbstractItemView, parent=None):
        super().__init__(parent)
        self._view = view

    def eventFilter(self, obj, event):
        if obj is self._view.viewport() and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                idx = self._view.indexAt(pos)
                if not idx.isValid():
                    self._view.clearSelection()
        return False


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
        self.add_scope_btn.setToolTip("将列表选中（无选中则当前可见全部）累加进工作范围")
        self.add_scope_btn.clicked.connect(self.accumulate_selected_to_scope)

        self.delete_btn = QPushButton("取消入库")
        self.delete_btn.setToolTip("从素材库移除记录（不删除磁盘视频文件）")
        self.delete_btn.clicked.connect(self.uncatalog_selected)

        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.filter_btn)
        toolbar.addWidget(self.view_switch_btn)
        toolbar.addWidget(self.delete_btn)
        toolbar.addWidget(self.add_scope_btn)
        layout.addLayout(toolbar)

        self.filter_panel = FilterPanel(self.settings, service=self.service)
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
        header.setSectionResizeMode(COL_FILENAME, QHeaderView.Stretch)
        self.table_view.setColumnWidth(COL_LIST_NO, 40)
        self.table_view.setColumnWidth(COL_LIBRARY_ID, 70)
        self.table_view.setColumnWidth(COL_THUMB, 140)
        self.table_view.setColumnWidth(COL_STATUS, 80)
        self.table_view.setItemDelegateForColumn(COL_THUMB, ThumbnailDelegate(self.table_view))
        self.table_view.setItemDelegateForColumn(COL_STATUS, StatusDelegate(self.table_view))
        self.table_view.setItemDelegateForColumn(COL_TAGS, MaterialTagsColumnDelegate(self.table_view))
        self.table_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.proxy_model.layoutChanged.connect(self._refresh_list_numbers)
        self.proxy_model.modelReset.connect(self._refresh_list_numbers)
        self.proxy_model.rowsInserted.connect(lambda *_: self._refresh_list_numbers())
        self.proxy_model.rowsRemoved.connect(lambda *_: self._refresh_list_numbers())
        header.sortIndicatorChanged.connect(lambda *_: self._refresh_list_numbers())
        self.view_stack.addWidget(self.table_view)

        self.card_view = QListView()
        self.card_view.setModel(self.proxy_model)
        self.card_view.setViewMode(QListView.IconMode)
        self.card_view.setResizeMode(QListView.Adjust)
        self.card_view.setMovement(QListView.Static)
        self.card_view.setSpacing(10)
        self.card_view.setModelColumn(COL_FILENAME)
        self.card_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.card_view.setItemDelegate(CardDelegate(self.card_view))
        self.card_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.view_stack.addWidget(self.card_view)

        self._wire_selection_ux(self.table_view)
        self._wire_selection_ux(self.card_view)

        self.splitter.addWidget(self.view_stack)
        self.detail_panel = DetailPanel(self.service)
        self.detail_panel.setMinimumWidth(320)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setMaximumHeight(22)
        layout.addWidget(self.status_label)

        expanded = self.settings.get("ui_preferences", {}).get("detail_panel_expanded", True)
        if not expanded:
            self.detail_panel.setVisible(False)

    def _wire_selection_ux(self, view: QAbstractItemView):
        view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        filt = _ClearSelectionOnEmptyClickFilter(view, self)
        view.viewport().installEventFilter(filt)
        if not hasattr(self, "_selection_filters"):
            self._selection_filters = []
        self._selection_filters.append(filt)
        sc = QShortcut(QKeySequence.SelectAll, view)
        sc.setContext(Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(view.selectAll)
        if not hasattr(self, "_selection_shortcuts"):
            self._selection_shortcuts = []
        self._selection_shortcuts.append(sc)

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
        self._refresh_list_numbers()

    def apply_advanced_filter(self, params):
        self.proxy_model.set_filter_params(params)
        self._refresh_list_numbers()

    def _refresh_list_numbers(self):
        """按当前可见顺序刷新列表序号；防重入，避免卡死 UI。"""
        if getattr(self, "_list_no_refreshing", False):
            return
        if getattr(self, "_list_no_refresh_scheduled", False):
            return
        self._list_no_refresh_scheduled = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_refresh_list_numbers)

    def _do_refresh_list_numbers(self):
        self._list_no_refresh_scheduled = False
        if getattr(self, "_list_no_refreshing", False):
            return
        self._list_no_refreshing = True
        proxy = self.proxy_model
        was_dynamic = True
        try:
            was_dynamic = proxy.dynamicSortFilter()
            proxy.setDynamicSortFilter(False)
        except Exception:
            pass
        try:
            mapping = {}
            for visual in range(proxy.rowCount()):
                src = proxy.mapToSource(proxy.index(visual, 0)).row()
                mapping[src] = visual + 1
            self.model.set_list_numbers(mapping)
        finally:
            try:
                proxy.setDynamicSortFilter(was_dynamic)
            except Exception:
                pass
            self._list_no_refreshing = False

    def load_data(self):
        videos = self.service.get_all_videos()
        self.model.update_data(videos)
        self._refresh_list_numbers()
        self.status_label.setText(f"素材库共 {len(videos)} 条")
        self.status_message.emit(self.status_label.text())

    def _get_selected_paths(self) -> list:
        if self.view_stack.currentWidget() == self.card_view:
            indexes = self.card_view.selectionModel().selectedIndexes()
        else:
            indexes = self.table_view.selectionModel().selectedRows()
        paths = []
        seen = set()
        for idx in indexes:
            src = self.proxy_model.mapToSource(self.proxy_model.index(idx.row(), 0))
            if not (0 <= src.row() < len(self.model.videos)):
                continue
            p = self.model.videos[src.row()].get("path")
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
        return paths

    def _get_visible_paths(self) -> list:
        paths = []
        for visual in range(self.proxy_model.rowCount()):
            src = self.proxy_model.mapToSource(self.proxy_model.index(visual, 0)).row()
            if not (0 <= src < len(self.model.videos)):
                continue
            p = self.model.videos[src].get("path")
            if p:
                paths.append(p)
        return paths

    def _resolve_batch_targets(self) -> list:
        """素材库操作目标：有选中用选中，无选中用可见；无工作范围约束。"""
        return self.service.resolve_operation_target_paths(
            self._get_selected_paths() or None,
            self._get_visible_paths(),
            scope_paths=None,
        )

    def uncatalog_selected(self):
        """素材库删除 = 取消入库（操作目标集）。"""
        paths = self._resolve_batch_targets()
        if not paths:
            QMessageBox.warning(
                self, "提示",
                "没有可取消入库的视频。\n请选中目标，或清空筛选使列表有可见项（无选中则作用于当前可见全部）。",
            )
            return
        n = len(paths)
        reply = QMessageBox.question(
            self,
            "取消入库",
            f"确定从素材库取消入库 {n} 条记录？\n"
            f"仅移除素材库入库记录，磁盘上的视频文件不会被删除。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.service.uncatalog_videos(paths)
        self.load_data()
        self.work_scope_changed.emit()

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

    def accumulate_selected_to_scope(self):
        paths = self._resolve_batch_targets()
        if not paths:
            QMessageBox.warning(
                self, "提示",
                "没有可累加的视频。\n请选中目标，或确保当前可见列表非空（无选中则累加可见全部）。",
            )
            return
        result = self.service.append_work_scope(paths, scan=True)
        n = len(result.get("paths") or [])
        msg = f"已累加 {len(paths)} 条到工作范围，现共 {n} 项路径"
        self.status_label.setText(msg)
        self.status_message.emit(msg)
        self.work_scope_changed.emit()
        QMessageBox.information(self, "完成", msg)