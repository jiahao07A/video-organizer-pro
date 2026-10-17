# -*- coding: utf-8 -*-
import os
import sys
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QSplitter, QStackedWidget, QTableView, QListView,
    QHeaderView, QAbstractItemView, QProgressBar, QMessageBox, QMenu,
    QDialog, QFileDialog, QTreeView, QListView as QtListView, QCheckBox,
    QToolButton, QSizePolicy, QTableWidget, QTableWidgetItem, QDialogButtonBox,
)
from PySide6.QtCore import Qt, Signal, QItemSelectionModel, QEvent, QObject, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from ..widgets.filter_panel import FilterPanel
from ..widgets.detail_panel import DetailPanel
from ..widgets.delegates import CardDelegate, MaterialTagsColumnDelegate, StatusDelegate, ThumbnailDelegate
from ..models.video_table import (
    VideoTableModel,
    CATALOG_SORT_COLUMNS,
    COL_FILENAME,
    COL_LIBRARY_ID,
    COL_LIST_NO,
    COL_STATUS,
    COL_THUMB,
    COL_CATEGORY,
    COL_TAGS,
    migrate_table_column_prefs,
)
from ..models.proxy_model import AdvancedSortFilterProxyModel
from ..workers.analysis_worker import AnalysisWorker
from ..workers.rename_worker import RenameWorker
from ..workers.io_worker import run_io_job
from ..widgets.rename_dialog import BatchRenameDialog
from core.video_organizer_service import VideoOrganizerService, SettingsManager
from core.organization_plan import OrganizationPlan, PHYSICAL_MIGRATION
from gui.styles import normalize_theme
from ..workers.catalog_worker import run_catalog_query


class _ClearSelectionOnEmptyClickFilter(QObject):
    """点空白区域清除选中（资源管理器常见交互）。"""

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


class PhysicalMigrationPreviewDialog(QDialog):
    """显示物理整理方案，支持按素材排除；不执行任何文件操作。"""

    _COLUMNS = ("执行", "状态", "源路径", "请求目标路径", "实际目标路径", "风险")

    def __init__(
        self,
        plan,
        target_root: str,
        parent=None,
        *,
        target_paths=(),
        work_scope_paths=(),
        target_summary: str = "",
        filter_summary: str = "未筛选",
    ):
        super().__init__(parent)
        self.setWindowTitle("模拟预览 — 物理整理")
        self.resize(1150, 640)
        self._raw_plan = list(plan or [])
        self._plan = OrganizationPlan.from_migration_plan(
            self._raw_plan,
            target_paths=target_paths,
            work_scope_paths=work_scope_paths,
            action_type=PHYSICAL_MIGRATION,
            filter_summary=filter_summary,
        )
        self._target_root = target_root
        self._target_summary = target_summary

        layout = QVBoxLayout(self)
        self.target_summary_label = QLabel(self._target_summary)
        self.target_summary_label.setWordWrap(True)
        if self._target_summary:
            layout.addWidget(self.target_summary_label)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.operation_checkbox = QCheckBox("执行物理整理（取消后本批次不执行此类操作）")
        self.operation_checkbox.setChecked(True)
        self.operation_checkbox.stateChanged.connect(self._update_summary)
        layout.addWidget(self.operation_checkbox)

        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("搜索源路径、目标路径、分类或风险…")
        self.filter_edit.textChanged.connect(self._filter_rows)
        layout.addWidget(self.filter_edit)

        self.table = QTableWidget(0, len(self._COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(self._COLUMNS))
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 360)
        self.table.setColumnWidth(3, 360)
        self.table.setColumnWidth(4, 360)
        self._populate_rows()
        self.table.itemChanged.connect(self._update_summary)
        layout.addWidget(self.table, 1)

        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No, parent=self)
        buttons.button(QDialogButtonBox.Yes).setText("确认执行物理整理")
        buttons.button(QDialogButtonBox.No).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.confirm_button = buttons.button(QDialogButtonBox.Yes)
        layout.addWidget(buttons)
        self._update_summary()

    def _populate_rows(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._plan.items))
        for row_index, step in enumerate(self._plan.items):
            execute_item = QTableWidgetItem()
            execute_item.setFlags(
                execute_item.flags() | Qt.ItemIsUserCheckable
            )
            execute_item.setCheckState(Qt.Checked)
            execute_item.setToolTip("取消勾选可排除该素材")
            self.table.setItem(row_index, 0, execute_item)

            risks = "；".join(step.risk_flags) or "无"
            values = (
                {"ready": "可执行", "review": "需复核", "blocked": "已阻止"}.get(
                    step.status, step.status or "未知"
                ),
                step.source_path,
                "",
                step.target_path,
                risks,
            )
            # 物理整理的完整请求目标路径由原始方案字段提供，便于确认实际改名。
            raw = (self._raw_plan or [])[row_index] if row_index < len(self._raw_plan) else {}
            values = (values[0], values[1], raw.get("requested_target_path", ""), values[3], values[4])
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)

    def _current_plan(self):
        if not self.operation_checkbox.isChecked():
            return self._plan.exclude_action_types([PHYSICAL_MIGRATION])
        checked_paths = set(self._checked_paths())
        excluded_paths = {
            item.source_path
            for item in self._plan.items
            if item.source_path not in checked_paths
        }
        return self._plan.exclude_paths(excluded_paths)

    def _update_summary(self, *_args):
        current = self._current_plan()
        active_count = len(current.active_items)
        conflicts = sum(1 for step in current.active_items if step.target_conflict)
        blocked = sum(1 for step in current.active_items if step.status == "blocked")
        summary = self._plan.summary()
        target_count = summary["target_count"]
        scope_count = summary["work_scope_count"]
        self.summary_label.setText(
            f"整理方案（尚未执行）：工作范围素材 {scope_count} 条；"
            f"操作目标集 {target_count} 条；方案条目 {summary['total_items']} 条；"
            f"当前选择 {active_count} 条（可执行 {active_count - blocked} 条，阻止 {blocked} 条），"
            f"已排除 {len(self._plan.items) - active_count} 条；"
            f"目标根目录：{self._target_root}；冲突 {conflicts} 条；"
            f"筛选状态：{summary['filter_summary']}。"
        )
        self.note_label.setText(
            "执行前会再次检查当前文件状态。"
            + (f" 当前有 {blocked} 条被阻止，不能执行。" if blocked else "")
            + " 本轮不提供对原始素材的全部恢复承诺。"
        )
        self.confirm_button.setEnabled(bool(active_count) and blocked == 0)

    def _checked_paths(self):
        if not self.operation_checkbox.isChecked():
            return []
        return [
            self.table.item(row, 2).text()
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == Qt.Checked
        ]

    def execution_paths(self):
        """返回本次确认后传给既有执行函数的源路径快照。"""
        return list(self._current_plan().active_source_paths)

    def confirmed_plan(self):
        """返回确认时的不可变方案快照，供结果摘要复用。"""
        return self._current_plan()

    def _filter_rows(self, text: str):
        query = text.strip().casefold()
        for row in range(self.table.rowCount()):
            content = " ".join(
                self.table.item(row, column).text()
                for column in range(1, self.table.columnCount())
                if self.table.item(row, column) is not None
            ).casefold()
            self.table.setRowHidden(row, bool(query and query not in content))


class WorkstationView(QWidget):
    """工作台视图 - 核心视频处理区域（仅呈现当前工作范围）"""
    video_selected = Signal(dict)  # 选中视频信号
    status_message = Signal(str)  # 状态栏消息信号
    progress_updated = Signal(int)  # 进度值信号

    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = service.settings
        self.worker = None
        self._query_generation = 0
        self._detail_generation = 0
        self._page_offset = 0
        self._page_total = 0
        self._page_limit = 100
        self._page_loading = False
        self._order_by = "timestamp"
        self._descending = True
        self._filter_params = {}
        self._query_debounce = QTimer(self)
        self._query_debounce.setSingleShot(True)
        self._query_debounce.setInterval(180)
        self._query_debounce.timeout.connect(self._load_query_page)
        self._query_pending = False
        self.setup_ui()
        self._refresh_scope_path_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 1. 顶部工具栏
        toolbar = QHBoxLayout()

        self.path_input = QLineEdit()
        self.path_input.setReadOnly(True)
        self.path_input.setPlaceholderText("尚未设定工作范围 — 请点「浏览」选择文件/文件夹")
        self.path_input.setToolTip("当前工作范围路径（由「浏览」整份替换设定）")

        browse_btn = QPushButton("浏览")
        browse_btn.setToolTip("多选文件与文件夹，确认为整份替换工作范围")
        browse_btn.clicked.connect(self.browse_path)

        accumulate_btn = QPushButton("累加")
        accumulate_btn.setToolTip("再选文件/文件夹，追加进当前工作范围")
        accumulate_btn.clicked.connect(self.accumulate_path)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索视频...")
        self.search_input.setFixedWidth(200)
        self.search_input.textChanged.connect(self.filter_text_changed)

        self.filter_btn = QPushButton("高级筛选")
        self.filter_btn.setCheckable(True)
        self.filter_btn.setChecked(False)
        self.filter_btn.clicked.connect(self.toggle_filter_panel)

        self.view_switch_btn = QPushButton("切换视图")
        self.view_switch_btn.setCheckable(True)
        self.view_switch_btn.clicked.connect(self.toggle_view_mode)
        self.view_switch_btn.setToolTip("切换列表/网格视图")

        self.analyze_btn = QPushButton("开始分析")
        self.analyze_btn.setObjectName("primary_button")
        self.analyze_btn.setStyleSheet("background-color: #3d5afe; color: white; font-weight: bold;")
        self.analyze_btn.clicked.connect(self.start_analysis)

        self.cancel_analysis_btn = QPushButton("取消分析")
        self.cancel_analysis_btn.setEnabled(False)
        self.cancel_analysis_btn.setToolTip("协作式取消：停止排队与批次补跑，已成功结果保留")
        self.cancel_analysis_btn.clicked.connect(self.cancel_analysis)

        self.force_reanalyze_cb = QCheckBox("强制重新分析")
        self.force_reanalyze_cb.setToolTip("勾选后对已分析视频也重新分析，并整份覆盖分类/摘要/标签")

        self.delete_btn = QPushButton("移出工作范围")
        self.delete_btn.setToolTip("从当前工作范围移除选中/可见目标（保留库记录与磁盘文件）")
        self.delete_btn.clicked.connect(self.delete_selected)

        self.cols_btn = QToolButton()
        self.cols_btn.setText("列显示")
        self.cols_btn.setPopupMode(QToolButton.InstantPopup)
        self.cols_btn.setToolTip("显示/隐藏列表列")

        toolbar.addWidget(QLabel("路径:"))
        toolbar.addWidget(self.path_input)
        toolbar.addWidget(browse_btn)
        toolbar.addWidget(accumulate_btn)
        toolbar.addSpacing(20)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.filter_btn)
        toolbar.addWidget(self.view_switch_btn)
        toolbar.addWidget(self.cols_btn)
        toolbar.addWidget(self.force_reanalyze_cb)
        toolbar.addWidget(self.analyze_btn)
        toolbar.addWidget(self.cancel_analysis_btn)
        toolbar.addWidget(self.delete_btn)

        layout.addLayout(toolbar)

        self.target_summary_label = QLabel("操作目标集：正在计算…")
        self.target_summary_label.setObjectName("operation_target_summary")
        self.target_summary_label.setStyleSheet("color: #888; padding: 2px 4px;")
        self.target_summary_label.setToolTip(
            "批量操作规则：有列表选中时使用列表选中；无选中时使用当前可见列表全部。工作台目标不得越出工作范围。"
        )
        layout.addWidget(self.target_summary_label)

        # 2. 筛选面板
        self.filter_panel = FilterPanel(self.settings, service=self.service)
        self.filter_panel.setVisible(False)
        self.filter_panel.filterChanged.connect(self.apply_advanced_filter)
        layout.addWidget(self.filter_panel)

        # 3. 中间区域
        self.splitter = QSplitter(Qt.Horizontal)
        self.view_stack = QStackedWidget()

        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self.table_view = QTableView()
        self.model = VideoTableModel()
        self.proxy_model = AdvancedSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterKeyColumn(-1)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)

        self.table_view.setModel(self.proxy_model)
        # SQL catalog owns ordering for the loaded window.
        self.table_view.setSortingEnabled(False)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.verticalHeader().setDefaultSectionSize(80)

        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(COL_FILENAME, QHeaderView.Stretch)
        header.sectionResized.connect(self._on_column_resized)
        header.sectionDoubleClicked.connect(self._on_header_double_clicked)
        self.table_view.setColumnWidth(COL_LIST_NO, 40)
        self.table_view.setColumnWidth(COL_LIBRARY_ID, 70)
        self.table_view.setColumnWidth(COL_THUMB, 140)
        self.table_view.setColumnWidth(COL_STATUS, 80)

        self.thumb_delegate = ThumbnailDelegate()
        self.status_delegate = StatusDelegate()
        self.tags_delegate = MaterialTagsColumnDelegate()
        self.table_view.setItemDelegateForColumn(COL_THUMB, self.thumb_delegate)
        self.table_view.setItemDelegateForColumn(COL_STATUS, self.status_delegate)
        self.table_view.setItemDelegateForColumn(COL_TAGS, self.tags_delegate)

        self.table_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self.show_context_menu)
        self.proxy_model.layoutChanged.connect(self._refresh_list_numbers)
        self.proxy_model.modelReset.connect(self._refresh_list_numbers)
        self.proxy_model.rowsInserted.connect(lambda *_: self._refresh_list_numbers())
        self.proxy_model.rowsRemoved.connect(lambda *_: self._refresh_list_numbers())
        header.setSortIndicator(-1, Qt.DescendingOrder)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sortIndicatorChanged.connect(self._on_catalog_sort_changed)
        self.table_view.verticalScrollBar().valueChanged.connect(self._maybe_fetch_more)

        table_layout.addWidget(self.table_view)

        self.card_view = QListView()
        self.card_view.setViewMode(QListView.IconMode)
        self.card_view.setResizeMode(QListView.Adjust)
        self.card_view.setUniformItemSizes(True)
        self.card_view.setWordWrap(True)
        self.card_view.setSpacing(10)
        self.card_view.setModel(self.proxy_model)
        self.card_view.setModelColumn(COL_FILENAME)
        self.card_view.setItemDelegate(CardDelegate(self.card_view))
        self.card_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.card_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.card_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.card_view.customContextMenuRequested.connect(self.show_context_menu)
        self.card_view.verticalScrollBar().valueChanged.connect(
            lambda value: self._maybe_fetch_more(value, self.card_view)
        )

        self.view_stack.addWidget(table_container)
        self.view_stack.addWidget(self.card_view)

        self._wire_selection_ux(self.table_view)
        self._wire_selection_ux(self.card_view)

        self.detail_panel = DetailPanel(self.service)
        self.detail_panel.data_changed.connect(self.load_data)
        self.detail_panel.setMinimumWidth(320)

        self.splitter.addWidget(self.view_stack)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        layout.addWidget(self.splitter, 1)

        controls_layout = QHBoxLayout()
        self.simulate_btn = QPushButton("模拟重命名")
        self.apply_btn = QPushButton("应用重命名")
        self.rollback_btn = QPushButton("回滚")
        self.export_fcpx_btn = QPushButton("导出 FCPX")
        self.export_ale_btn = QPushButton("导出 ALE (达芬奇)")
        self.sync_xmp_btn = QPushButton("同步元数据 (XMP)")
        self.sync_xmp_btn.setStyleSheet("background-color: #2e7d32; color: white;")

        self.auto_organize_btn = QPushButton("自动分类整理")
        self.auto_organize_btn.setStyleSheet("background-color: #ff9800; color: white; font-weight: bold;")
        self.auto_organize_btn.clicked.connect(self.start_auto_organize)

        self.simulate_btn.clicked.connect(lambda: self.start_rename(dry_run=True))
        self.apply_btn.clicked.connect(lambda: self.start_rename(dry_run=False))
        self.rollback_btn.clicked.connect(self.rollback_rename)
        self.export_fcpx_btn.clicked.connect(self.export_fcpx)
        self.export_ale_btn.clicked.connect(self.export_ale)
        self.sync_xmp_btn.clicked.connect(self.sync_metadata)

        controls_layout.addWidget(self.simulate_btn)
        controls_layout.addWidget(self.apply_btn)
        controls_layout.addWidget(self.rollback_btn)
        controls_layout.addWidget(self.export_fcpx_btn)
        controls_layout.addWidget(self.export_ale_btn)
        controls_layout.addWidget(self.sync_xmp_btn)
        controls_layout.addWidget(self.auto_organize_btn)
        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        progress_layout = QVBoxLayout()
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(2)
        self.status_label = QLabel("准备就绪")
        self.status_label.setMaximumHeight(22)
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, 0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumHeight(14)
        self.progress_bar.setFormat("总 %p%")
        self.sub_progress_bar = QProgressBar()
        self.sub_progress_bar.setVisible(False)
        self.sub_progress_bar.setMaximumHeight(14)
        self.sub_progress_bar.setFormat("本轮 %p%")
        bar_row.addWidget(QLabel("总"))
        bar_row.addWidget(self.progress_bar, 1)
        bar_row.addWidget(QLabel("本轮"))
        bar_row.addWidget(self.sub_progress_bar, 1)
        progress_layout.addWidget(self.status_label)
        progress_layout.addLayout(bar_row)
        layout.addLayout(progress_layout)

        self._build_column_menu()
        self._apply_column_preferences()
        self._restore_column_widths()

    def _wire_selection_ux(self, view: QAbstractItemView):
        """ExtendedSelection + Ctrl+A + 点空白 clearSelection。"""
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

    def _active_item_view(self) -> QAbstractItemView:
        if self.view_stack.currentWidget() == self.card_view:
            return self.card_view
        return self.table_view

    def _get_selected_paths(self) -> list:
        """当前列表/卡片选中的视频路径（去重，保持顺序）。"""
        view = self._active_item_view()
        if view is self.card_view:
            indexes = view.selectionModel().selectedIndexes()
        else:
            indexes = view.selectionModel().selectedRows()
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
        """Resolve all rows matching the current query, not only loaded rows."""
        return self.service.resolve_visible_video_paths(
            self._current_filter_params(),
            scope_paths=self._work_scope_query_paths(),
            order_by=self._order_by,
            descending=self._descending,
        )

    def _current_filter_params(self) -> dict:
        params = dict(self._filter_params or {})
        params["text"] = self.search_input.text()
        return params

    def _scope_paths(self) -> list:
        """工作范围内已入库视频路径，供操作目标集求交。"""
        return self.service.get_work_scope_video_paths()

    def _work_scope_query_paths(self) -> list:
        """原始工作范围路径，供 SQL 前缀查询；不展开成全部视频路径。"""
        return self.service.get_work_scope_paths()

    def _resolve_batch_snapshot(self):
        """解析一次操作目标集快照，供摘要、确认和执行共用。"""
        return self.service.resolve_operation_target_snapshot(
            self._get_selected_paths(),
            self._get_visible_paths(),
            scope_paths=self._scope_paths(),
        )

    def _filter_summary_text(self) -> str:
        params = self._current_filter_params()
        active = any(
            bool(value) and value not in ([], (), "")
            for value in params.values()
        )
        return "筛选已启用" if active else "未筛选"

    def _format_operation_target_snapshot(self, snapshot) -> str:
        source = (
            f"列表选中 {snapshot.selected_count} 条"
            if snapshot.source == "selected"
            else "当前可见列表全部"
        )
        scope = ""
        if snapshot.scope_paths is not None:
            scope = f" · 工作范围内可见 {len(snapshot.scope_paths)} 条"
        return (
            f"操作目标集：{source} · 可见列表 {snapshot.visible_count} 条"
            f" · 实际处理 {snapshot.target_count} 条{scope} · {self._filter_summary_text()}"
        )

    def _refresh_operation_target_summary(self):
        try:
            snapshot = self._resolve_batch_snapshot()
            self.target_summary_label.setText(
                self._format_operation_target_snapshot(snapshot)
            )
        except Exception as exc:
            self.target_summary_label.setText(f"操作目标集：暂时无法计算（{exc}）")

    def _resolve_batch_targets(self) -> list:
        """批量操作目标：返回当前快照中的实际路径。"""
        return list(self._resolve_batch_snapshot().target_paths)

    def _build_column_menu(self):
        menu = QMenu(self)
        raw_prefs = (self.settings.get("ui_preferences") or {}).get("table_columns_visible") or {}
        prefs = migrate_table_column_prefs(raw_prefs, self.model.columnCount())
        defaults = {
            COL_LIST_NO: True,
            COL_LIBRARY_ID: True,
            COL_THUMB: True,
            COL_FILENAME: True,
            COL_CATEGORY: True,
            COL_TAGS: True,
            COL_STATUS: True,
        }
        labels = list(self.model.headers)
        self._col_actions = {}
        for i, lab in enumerate(labels):
            act = menu.addAction(lab)
            act.setCheckable(True)
            act.setChecked(prefs.get(str(i), defaults.get(i, True)))
            act.toggled.connect(lambda checked, col=i: self._set_column_visible(col, checked))
            self._col_actions[i] = act
        reset_act = menu.addAction("重置列宽")
        reset_act.triggered.connect(self._reset_column_widths)
        self.cols_btn.setMenu(menu)

    def _set_column_visible(self, col: int, visible: bool):
        if col < 0 or col >= self.model.columnCount():
            return
        self.table_view.setColumnHidden(col, not visible)
        prefs = self.settings.setdefault("ui_preferences", {})
        cols = prefs.setdefault("table_columns_visible", {})
        cols[str(col)] = visible
        cols["_schema"] = "no_check_col"
        try:
            SettingsManager.save_settings(self.settings, self.service.db)
        except Exception:
            pass

    def _apply_column_preferences(self):
        raw = (self.settings.get("ui_preferences") or {}).get("table_columns_visible") or {}
        prefs = migrate_table_column_prefs(raw, self.model.columnCount())
        for i in range(self.model.columnCount()):
            show = prefs.get(str(i), True)
            self.table_view.setColumnHidden(i, not show)
        if raw != prefs or raw.get("_schema") != "no_check_col":
            try:
                to_store = dict(prefs)
                to_store["_schema"] = "no_check_col"
                self.settings.setdefault("ui_preferences", {})["table_columns_visible"] = to_store
                SettingsManager.save_settings(self.settings, self.service.db)
            except Exception:
                pass

    def _on_column_resized(self, logical, _old, new):
        if logical < 0 or logical >= self.model.columnCount():
            return
        prefs = self.settings.setdefault("ui_preferences", {})
        widths = prefs.setdefault("table_column_widths", {})
        widths[str(logical)] = new
        try:
            SettingsManager.save_settings(self.settings, self.service.db)
        except Exception:
            pass

    def _restore_column_widths(self):
        raw = (self.settings.get("ui_preferences") or {}).get("table_column_widths") or {}
        widths = migrate_table_column_prefs(raw, self.model.columnCount())
        for k, w in widths.items():
            try:
                col = int(k)
                if 0 <= col < self.model.columnCount():
                    self.table_view.setColumnWidth(col, int(w))
            except Exception:
                pass

    def _reset_column_widths(self):
        defaults = {
            COL_LIST_NO: 40,
            COL_LIBRARY_ID: 70,
            COL_THUMB: 140,
            COL_FILENAME: 200,
            COL_CATEGORY: 100,
            COL_TAGS: 150,
            COL_STATUS: 80,
        }
        for c, w in defaults.items():
            self.table_view.setColumnWidth(c, w)
        prefs = self.settings.setdefault("ui_preferences", {})
        prefs["table_column_widths"] = {str(k): v for k, v in defaults.items()}
        try:
            SettingsManager.save_settings(self.settings, self.service.db)
        except Exception:
            pass

    def _on_header_double_clicked(self, logical):
        if 0 <= logical < self.model.columnCount():
            self.table_view.resizeColumnToContents(logical)

    def _refresh_list_numbers(self):
        """按当前可见顺序刷新列表序号；防重入，避免卡死 UI。"""
        if getattr(self, "_list_no_refreshing", False):
            return
        if getattr(self, "_list_no_refresh_scheduled", False):
            return
        # 合并同事件循环内多次 layoutChanged/rows* 信号
        self._list_no_refresh_scheduled = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_refresh_list_numbers)

    def _do_refresh_list_numbers(self):
        self._list_no_refresh_scheduled = False
        if getattr(self, "_list_no_refreshing", False):
            return
        self._list_no_refreshing = True
        proxy = self.proxy_model
        # 更新「#」列 DisplayRole 时禁止代理按该列动态重排，否则会无限递归
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

    def toggle_filter_panel(self):
        self.filter_panel.setVisible(self.filter_btn.isChecked())

    def apply_theme(self, theme=None):
        """主窗主题变更时刷新高级筛选等局部面板。"""
        if theme is None:
            theme = self.settings.get("ui_preferences", {}).get("theme", "dark")
        theme = normalize_theme(theme)
        if hasattr(self, "filter_panel") and hasattr(self.filter_panel, "apply_theme"):
            self.filter_panel.apply_theme(theme)

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

    def toggle_view_mode(self):
        if self.view_switch_btn.isChecked():
            self.card_view.selectionModel().clearSelection()
            for idx in self.table_view.selectionModel().selectedRows():
                self.card_view.selectionModel().select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.view_stack.setCurrentWidget(self.card_view)
            self.view_switch_btn.setText("切换列表")
        else:
            self.table_view.selectionModel().clearSelection()
            for idx in self.card_view.selectionModel().selectedIndexes():
                self.table_view.selectionModel().select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.view_stack.setCurrentIndex(0)
            self.view_switch_btn.setText("切换视图")

    def filter_text_changed(self, text):
        self._filter_params = dict(self._filter_params)
        self._filter_params["text"] = text
        self.proxy_model.set_filter_params({})
        self._refresh_operation_target_summary()
        self._schedule_query_reload()

    def apply_advanced_filter(self, params):
        self._filter_params = dict(params or {})
        self._filter_params["text"] = self.search_input.text()
        self.proxy_model.set_filter_params({})
        self._refresh_operation_target_summary()
        self._schedule_query_reload()

    def _on_catalog_sort_changed(self, column, order):
        # 缩略图和标签列没有定义 SQL 排序，保留当前有效排序指示。
        if column not in CATALOG_SORT_COLUMNS and column != -1:
            header = self.table_view.horizontalHeader()
            header.blockSignals(True)
            previous = next((c for c, key in CATALOG_SORT_COLUMNS.items() if key == self._order_by), -1)
            header.setSortIndicator(previous, Qt.DescendingOrder if self._descending else Qt.AscendingOrder)
            header.blockSignals(False)
            return
        self._order_by = CATALOG_SORT_COLUMNS.get(column, "timestamp")
        self._descending = order == Qt.DescendingOrder
        self._refresh_operation_target_summary()
        self._schedule_query_reload()

    def _schedule_query_reload(self):
        self._query_pending = True
        self._query_debounce.start()

    def _load_query_page(self, *, reset=True):
        if not self._query_pending and reset:
            return
        self._query_pending = False
        scope = self._work_scope_query_paths()
        if reset:
            self._query_generation += 1
            generation = self._query_generation
            offset = 0
            self._page_offset = 0
            self._page_total = 0
        else:
            generation = self._query_generation
            offset = self._page_offset
        params = self._current_filter_params()
        self._page_loading = True
        if reset:
            self.status_label.setText("正在查询工作范围…")

        def on_ok(page):
            if generation != self._query_generation:
                return
            self._page_loading = False
            if reset:
                self.model.update_data(page.rows)
            else:
                self.model.append_data(page.rows)
            self._page_offset = page.offset + len(page.rows)
            self._page_total = page.total_count
            self._refresh_list_numbers()
            self._refresh_scope_path_ui()
            self._refresh_operation_target_summary()
            self.status_label.setText(f"工作范围内共 {page.total_count} 个已入库视频，已加载 {self._page_offset} 个")
            self.status_message.emit(self.status_label.text())

        def on_fail(message):
            if generation != self._query_generation:
                return
            self._page_loading = False
            self.status_label.setText(f"查询失败：{message}")
            self.status_message.emit(self.status_label.text())

        run_catalog_query(
            self,
            fn=lambda: self.service.query_video_page(
                params,
                scope_paths=scope,
                offset=offset,
                limit=self._page_limit,
                order_by=self._order_by,
                descending=self._descending,
            ),
            on_ok=on_ok,
            on_fail=on_fail,
        )

    def _maybe_fetch_more(self, value, view=None):
        view = view or self.table_view
        bar = view.verticalScrollBar()
        if self._query_pending or self._page_loading or self._page_offset >= self._page_total:
            return
        if value >= max(0, bar.maximum() - 12):
            self._load_query_page(reset=False)

    def _collect_dialog_paths(self, dialog: QFileDialog) -> list:
        """从非原生文件对话框收集多选路径（文件 + 文件夹）。"""
        found = []
        seen = set()

        def add(p):
            if not p:
                return
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:
                return
            seen.add(key)
            found.append(os.path.abspath(p))

        for p in dialog.selectedFiles():
            add(p)

        views = list(dialog.findChildren(QListView)) + list(dialog.findChildren(QTreeView))
        for view in views:
            model = view.model()
            sm = view.selectionModel()
            if model is None or sm is None:
                continue
            for idx in sm.selectedIndexes():
                if idx.column() != 0:
                    continue
                path = None
                if hasattr(model, "filePath"):
                    try:
                        path = model.filePath(idx)
                    except Exception:
                        path = None
                if not path:
                    data = model.data(idx, Qt.DisplayRole)
                    if data and os.path.isabs(str(data)):
                        path = str(data)
                add(path)
        return found

    def _refresh_scope_path_ui(self):
        """根据服务层工作范围刷新路径区摘要（多项显示数量 + tooltip 列表）。"""
        paths = self.service.get_work_scope_paths()
        if not paths:
            self.path_input.setText("")
            self.path_input.setPlaceholderText("尚未设定工作范围 — 请点「浏览」选择文件/文件夹")
            self.path_input.setToolTip("当前无工作范围")
            return
        if len(paths) == 1:
            self.path_input.setText(paths[0])
        else:
            self.path_input.setText(f"{len(paths)} 项已选")
        self.path_input.setToolTip("\n".join(paths))

    def load_data(self):
        """后台加载工作范围内的首批列表页。"""
        scope = self.service.get_work_scope_paths()
        self._refresh_scope_path_ui()
        if not scope:
            # 空范围也要使进行中的旧查询失效，防止结果迟到后恢复旧行。
            self._query_generation += 1
            self._detail_generation += 1
            self._page_loading = False
            self._page_offset = 0
            self._page_total = 0
            self._query_pending = False
            self._query_debounce.stop()
            self.model.update_data([])
            self._refresh_operation_target_summary()
            self.status_label.setText("请先设定工作范围：点击「浏览」选择文件或文件夹")
            self.status_message.emit(self.status_label.text())
            return
        self._query_pending = True
        self._load_query_page(reset=True)


    def on_selection_changed(self, selected, deselected):
        self._refresh_operation_target_summary()
        self._detail_generation += 1
        generation = self._detail_generation
        if self.view_stack.currentWidget() == self.card_view:
            indexes = self.card_view.selectionModel().selectedIndexes()
        else:
            indexes = self.table_view.selectionModel().selectedRows()
        self.detail_panel.save_btn.setEnabled(False)
        if not indexes:
            return
        rows = sorted(set(idx.row() for idx in indexes))
        if len(rows) > 1:
            selected_videos = [
                self.model.videos[self.proxy_model.mapToSource(self.proxy_model.index(row, 0)).row()]
                for row in rows
            ]
            self.detail_panel.load_video_data(selected_videos)
            return
        source_idx = self.proxy_model.mapToSource(self.proxy_model.index(rows[0], 0))
        path = self.model.videos[source_idx.row()]["path"]

        def on_ok(detail):
            if generation == self._detail_generation and detail:
                self.video_selected.emit(detail)
                self.detail_panel.load_video_data(detail)

        run_catalog_query(
            self,
            fn=lambda: self.service.get_video_detail(path),
            on_ok=on_ok,
            worker_key="_detail_worker",
        )

    def show_context_menu(self, pos):
        sender = self.sender()
        if not sender:
            return

        if sender == self.card_view:
            indexes = self.card_view.selectionModel().selectedIndexes()
        else:
            indexes = self.table_view.selectionModel().selectedRows()

        if not indexes:
            return

        menu = QMenu(self)
        analyze_action = menu.addAction("🔍 分析选中项")
        delete_action = menu.addAction("移出工作范围")
        menu.addSeparator()
        replace_tag_action = menu.addAction("🔄 批量替换标签")
        menu.addSeparator()
        folder_action = menu.addAction("📂 在文件夹中显示")
        play_action = menu.addAction("▶️ 播放/暂停预览")

        global_pos = sender.viewport().mapToGlobal(pos)
        action = menu.exec_(global_pos)

        if action == analyze_action:
            self.analyze_selected()
        elif action == delete_action:
            self.delete_selected()
        elif action == replace_tag_action:
            self.batch_replace_tags_ui()
        elif action == folder_action:
            self.open_selected_folder()
        elif action == play_action:
            self.detail_panel.on_thumb_double_click(None)

    def batch_replace_tags_ui(self):
        """弹出对话框进行批量标签替换（仅操作目标集）。"""
        from PySide6.QtWidgets import QInputDialog
        targets = self._resolve_batch_targets()
        if not targets:
            QMessageBox.warning(
                self, "提示",
                "没有可替换标签的目标。\n请设定工作范围，或选中条目（无选中则作用于当前可见列表）。",
            )
            return
        old_tag, ok1 = QInputDialog.getText(self, "批量替换标签", "请输入要替换的原标签:")
        if not ok1 or not old_tag:
            return

        new_tag, ok2 = QInputDialog.getText(self, "批量替换标签", f"将 '{old_tag}' 替换为:")
        if not ok2:
            return

        reply = QMessageBox.question(
            self, "确认替换",
            f"确定在当前操作目标集（{len(targets)} 条视频）中，\n"
            f"将标签 '{old_tag}' 替换为 '{new_tag}' 吗？\n"
            f"（不会修改范围外的全库其它条目）",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.service.bulk_replace_tags(old_tag, new_tag, selected_paths=targets)
            self.load_data()
            QMessageBox.information(self, "成功", f"已在 {len(targets)} 条目标中完成替换。")

    def background_scan_work_scope(self, reason: str = "扫盘入库"):
        """后台扫描当前工作范围并入库。"""
        def job():
            return self.service.scan_and_register_work_scope()

        def on_ok(registered):
            n_reg = len(registered or [])
            self.load_data()
            self.status_label.setText(f"{reason}完成；新入库未分析 {n_reg} 个")
            self.status_message.emit(self.status_label.text())
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.set_ui_enabled(True)

        def on_fail(msg):
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.set_ui_enabled(True)
            QMessageBox.warning(self, "扫盘失败", msg or "未知错误")

        def on_start():
            self.set_ui_enabled(False)
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            self.status_label.setText(f"{reason}中…")

        run_io_job(
            self, fn=job, on_ok=on_ok, on_fail=on_fail, on_start=on_start,
            busy_message=f"{reason}中…",
        )

    def browse_path(self):
        """多选文件与文件夹，确认后整份替换工作范围并后台扫盘入库。"""
        dialog = QFileDialog(self, "选择工作范围（可多选文件与文件夹，确认后整份替换）")
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, False)
        dialog.setNameFilters([
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.m4v *.flv *.wmv)",
            "所有文件 (*.*)",
        ])
        for view in dialog.findChildren(QTreeView):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for view in dialog.findChildren(QtListView):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        if dialog.exec() != QDialog.Accepted:
            return
        selected = self._collect_dialog_paths(dialog)
        if not selected:
            return

        # 先同步替换路径（轻量），扫盘后台做
        result = self.service.replace_work_scope(selected, scan=False)
        self._refresh_scope_path_ui()
        self.load_data()
        n_paths = len(result.get("paths") or [])
        self.status_label.setText(f"工作范围已替换为 {n_paths} 项；正在后台扫盘…")
        self.status_message.emit(self.status_label.text())
        self.background_scan_work_scope(reason="工作范围扫盘")

    def accumulate_path(self):
        """多选路径并累加进当前工作范围，后台扫盘。"""
        dialog = QFileDialog(self, "累加到工作范围（可多选文件与文件夹）")
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, False)
        dialog.setNameFilters([
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.m4v *.flv *.wmv)",
            "所有文件 (*.*)",
        ])
        for view in dialog.findChildren(QTreeView):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for view in dialog.findChildren(QtListView):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)

        if dialog.exec() != QDialog.Accepted:
            return
        selected = self._collect_dialog_paths(dialog)
        if not selected:
            return

        result = self.service.append_work_scope(selected, scan=False)
        self._refresh_scope_path_ui()
        self.load_data()
        n_paths = len(result.get("paths") or [])
        self.status_label.setText(f"工作范围已累加，现共 {n_paths} 项；正在后台扫盘…")
        self.status_message.emit(self.status_label.text())
        self.background_scan_work_scope(reason="累加扫盘")

    def get_selected_source_rows(self):
        if self.view_stack.currentWidget() == self.card_view:
            indexes = self.card_view.selectionModel().selectedIndexes()
        else:
            indexes = self.table_view.selectionModel().selectedRows()

        rows = sorted(list(set(idx.row() for idx in indexes)))
        source_rows = []
        for r in rows:
            proxy_idx = self.proxy_model.index(r, 0)
            src_idx = self.proxy_model.mapToSource(proxy_idx)
            source_rows.append(src_idx.row())
        return source_rows

    def analyze_selected(self):
        """右键：仅分析当前列表选中（非「可见全部」回退）。"""
        paths = self._get_selected_paths()
        if not paths:
            return
        force = getattr(self, "force_reanalyze_cb", None) and self.force_reanalyze_cb.isChecked()
        if force:
            reply = QMessageBox.question(
                self, "确认强制重新分析",
                f"将对选中的 {len(paths)} 个视频强制重新分析并覆盖结果。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.set_ui_enabled(False)
        self.cancel_analysis_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.sub_progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.sub_progress_bar.setVisible(True)
        self.worker = AnalysisWorker(self.service, paths, force_reanalyze=bool(force))
        self.worker.progress_updated.connect(self.update_status)
        self.worker.snapshot_updated.connect(self.on_analysis_snapshot)
        self.worker.task_finished.connect(self.on_task_finished)
        self.worker.start()

    def delete_selected(self):
        """工作台删除 = 移出工作范围（操作目标集）。"""
        snapshot = self._resolve_batch_snapshot()
        paths = list(snapshot.target_paths)
        if not paths:
            QMessageBox.warning(
                self, "提示",
                "没有可移出的视频。\n请先设定工作范围，或在列表中选中目标（无选中则作用于当前可见列表）。",
            )
            return
        n = len(paths)
        reply = QMessageBox.question(
            self, "移出工作范围",
            f"{self._format_operation_target_snapshot(snapshot)}\n\n"
            f"确定将 {n} 条移出当前工作范围？\n"
            f"仅从工作范围移除，素材库入库记录与磁盘文件都会保留。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.service.remove_videos_from_work_scope(paths)
            self.load_data()

    def open_selected_folder(self):
        source_rows = self.get_selected_source_rows()
        if not source_rows:
            return
        path = self.model.videos[source_rows[0]].get("path")
        if path and os.path.exists(path):
            folder = os.path.dirname(path)
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                import subprocess
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.call([opener, folder])

    def update_status(self, progress, message):
        if message:
            self.status_label.setText(message)
            self.status_message.emit(message)
        if progress >= 0:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(progress)
            self.progress_updated.emit(progress)

    def on_analysis_snapshot(self, snap: dict):
        """消费结构化分析进度快照（总/子进度 + 行临时态）。"""
        if not isinstance(snap, dict):
            return
        now = time.monotonic()
        last = getattr(self, "_last_snap_ui_mono", 0.0)
        force = bool(snap.get("cancelled"))
        overall = int(snap.get("overall_percent") or 0)
        sub = int(snap.get("sub_percent") or 0)
        msg = snap.get("message") or ""
        if not force and (now - last) < 0.2:
            # 轻量更新进度条，跳过整表 status 刷新
            self.progress_bar.setVisible(True)
            self.sub_progress_bar.setVisible(True)
            self.progress_bar.setValue(overall)
            self.sub_progress_bar.setValue(sub)
            if msg:
                self.status_label.setText(msg)
            return
        self._last_snap_ui_mono = now
        if msg:
            self.status_label.setText(msg)
            self.status_message.emit(msg)
        self.progress_bar.setVisible(True)
        self.sub_progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.sub_progress_bar.setRange(0, 100)
        self.progress_bar.setValue(overall)
        self.sub_progress_bar.setValue(sub)
        od, ot = snap.get("overall_done", 0), snap.get("overall_total", 0)
        sd, st = snap.get("sub_done", 0), snap.get("sub_total", 0)
        self.progress_bar.setFormat(f"总 {od}/{ot} (%p%)")
        kind = snap.get("round_kind") or "first"
        if kind == "rerun":
            self.sub_progress_bar.setFormat(f"补跑 {sd}/{st} (%p%)")
        else:
            self.sub_progress_bar.setFormat(f"本轮 {sd}/{st} (%p%)")
        self.progress_updated.emit(overall)
        rows = snap.get("rows") or {}
        if hasattr(self.model, "set_temp_status_map"):
            self.model.set_temp_status_map(rows)

    def cancel_analysis(self):
        w = getattr(self, "worker", None)
        if w is not None and hasattr(w, "request_cancel"):
            w.request_cancel()
            self.status_label.setText("正在取消分析…")
            self.cancel_analysis_btn.setEnabled(False)

    def set_ui_enabled(self, enabled):
        """分析/长任务期间锁定写盘与导出入口。"""
        self.analyze_btn.setEnabled(enabled)
        self.simulate_btn.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        self.rollback_btn.setEnabled(enabled)
        self.sync_xmp_btn.setEnabled(enabled)
        self.path_input.setEnabled(enabled)
        self.export_fcpx_btn.setEnabled(enabled)
        self.export_ale_btn.setEnabled(enabled)
        self.auto_organize_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)
        if hasattr(self, "detail_panel") and self.detail_panel:
            try:
                self.detail_panel.setEnabled(enabled)
            except Exception:
                pass
        if enabled:
            self.cancel_analysis_btn.setEnabled(False)

    def start_analysis(self):
        """分析操作目标集：有选中用选中，无选中用当前可见；⊆ 工作范围。"""
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先通过「浏览」设定工作范围。")
            return

        snapshot = self._resolve_batch_snapshot()
        paths = list(snapshot.target_paths)
        if not paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可分析的视频。")
            return

        force = getattr(self, "force_reanalyze_cb", None) and self.force_reanalyze_cb.isChecked()
        if force:
            reply = QMessageBox.question(
                self, "确认强制重新分析",
                f"{self._format_operation_target_snapshot(snapshot)}\n\n"
                f"将对 {len(paths)} 个目标强制重新分析并整份覆盖分类/摘要/标签。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.set_ui_enabled(False)
        self.cancel_analysis_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.sub_progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.sub_progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.sub_progress_bar.setRange(0, 100)
        if hasattr(self.model, "clear_temp_status_map"):
            self.model.clear_temp_status_map()

        self.worker = AnalysisWorker(self.service, paths, force_reanalyze=bool(force))
        self.worker.progress_updated.connect(self.update_status)
        self.worker.snapshot_updated.connect(self.on_analysis_snapshot)
        self.worker.task_finished.connect(self.on_task_finished)
        self.worker.start()

    def start_rename(self, dry_run=True):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return

        snapshot = self._resolve_batch_snapshot()
        selected_paths = list(snapshot.target_paths)
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可重命名的视频。")
            return

        selected_videos = self.service.get_videos_for_operation(
            selected_paths,
            selected_paths,
            scope_paths=self._scope_paths(),
        )
        if not selected_videos:
            QMessageBox.warning(self, "警告", "工作范围内没有可重命名的视频。")
            return

        dialog = BatchRenameDialog(self.service, selected_videos, self)
        if dialog.exec() != QDialog.Accepted:
            return

        config = dialog.get_final_config()

        if not dry_run:
            reply = QMessageBox.question(
                self, "确认",
                f"{self._format_operation_target_snapshot(snapshot)}\n\n"
                f"确定要对 {len(selected_paths)} 个文件应用重命名吗？文件将被物理重命名。",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        self.set_ui_enabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.worker = RenameWorker(
            self.service,
            dry_run=dry_run,
            selected_paths=selected_paths,
            pattern=config["pattern"],
            regex_find=config["find_regex"],
            regex_replace=config["replace_str"],
        )
        self.worker.progress_updated.connect(self.update_status)
        self.worker.task_finished.connect(self.on_task_finished)
        self.worker.start()

    def rollback_rename(self):
        if self.service.rollback_last_session():
            QMessageBox.information(self, "成功", "已成功回滚上一次重命名操作。")
            self.load_data()
        else:
            QMessageBox.warning(self, "错误", "没有可回滚的记录。")

    def _busy_io_start(self, msg: str = "处理中…"):
        self.set_ui_enabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(msg)

    def _busy_io_end(self):
        self.set_ui_enabled(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)

    def export_fcpx(self):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        snapshot = self._resolve_batch_snapshot()
        selected_paths = list(snapshot.target_paths)
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可导出的视频。")
            return
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 FCPX XML", "", "FCPXML Files (*.fcpxml)")
        if not file_path:
            return

        def job():
            self.service.export_to_fcpx_xml(file_path, selected_paths)
            return file_path

        def on_ok(path):
            self._busy_io_end()
            QMessageBox.information(self, "成功", f"FCPX XML 已导出至: {path}")

        def on_fail(msg):
            self._busy_io_end()
            QMessageBox.warning(self, "导出失败", msg or "未知错误")

        run_io_job(
            self, fn=job, on_ok=on_ok, on_fail=on_fail,
            on_start=lambda: self._busy_io_start("正在导出 FCPX…"),
            busy_message="正在导出 FCPX…",
        )

    def export_ale(self):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        snapshot = self._resolve_batch_snapshot()
        selected_paths = list(snapshot.target_paths)
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可导出的视频。")
            return
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 ALE (达芬奇)", "", "ALE Files (*.ale)")
        if not file_path:
            return

        def job():
            return self.service.export_to_ale(file_path, selected_paths), file_path

        def on_ok(result):
            self._busy_io_end()
            success, path = result if isinstance(result, tuple) else (result, file_path)
            if success:
                QMessageBox.information(
                    self, "成功",
                    f"ALE 文件已导出至: {path}\n请在达芬奇中使用 'File -> Import -> Metadata from ALE...' 导入。",
                )
            else:
                QMessageBox.warning(self, "失败", "导出 ALE 文件失败。")

        def on_fail(msg):
            self._busy_io_end()
            QMessageBox.warning(self, "导出失败", msg or "未知错误")

        run_io_job(
            self, fn=job, on_ok=on_ok, on_fail=on_fail,
            on_start=lambda: self._busy_io_start("正在导出 ALE…"),
            busy_message="正在导出 ALE…",
        )

    def sync_metadata(self):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        snapshot = self._resolve_batch_snapshot()
        selected_paths = list(snapshot.target_paths)
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可同步的视频。")
            return

        def job():
            return self.service.sync_metadata_to_xmp(selected_paths)

        def on_ok(count):
            self._busy_io_end()
            QMessageBox.information(self, "完成", f"已成功为 {count} 个视频生成 XMP 侧边文件。")

        def on_fail(msg):
            self._busy_io_end()
            QMessageBox.warning(self, "XMP 同步失败", msg or "未知错误")

        run_io_job(
            self, fn=job, on_ok=on_ok, on_fail=on_fail,
            on_start=lambda: self._busy_io_start("正在同步 XMP…"),
            busy_message="正在同步 XMP…",
        )

    def start_auto_organize(self):
        """物理整理：先模拟预览清单，确认后后台执行。"""
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        snapshot = self._resolve_batch_snapshot()
        selected_paths = list(snapshot.target_paths)
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可整理的视频。")
            return

        target_root = QFileDialog.getExistingDirectory(self, "选择整理后的目标根目录")
        if not target_root:
            return

        plan = self.service.plan_physical_migration(target_root, selected_paths)
        if not plan:
            QMessageBox.information(self, "无需整理", "没有需要移动的文件（可能已在目标位置或路径无效）。")
            return

        preview_dialog = PhysicalMigrationPreviewDialog(
            plan,
            target_root,
            self,
            target_paths=selected_paths,
            work_scope_paths=snapshot.scope_paths or (),
            target_summary=self._format_operation_target_snapshot(snapshot),
            filter_summary=self._filter_summary_text(),
        )
        if preview_dialog.exec() != QDialog.Accepted:
            return
        confirmed_plan = preview_dialog.confirmed_plan()
        execution_paths = list(confirmed_plan.active_source_paths)
        if not execution_paths:
            return

        def job():
            return self.service.execute_physical_migration(target_root, execution_paths)

        def on_ok(count):
            self._busy_io_end()
            self.load_data()
            summary = confirmed_plan.execution_summary(count)
            QMessageBox.information(
                self,
                "整理结果",
                "整理执行已结束（当前会话结果）\n"
                f"确认执行：{summary['requested_count']} 条\n"
                f"成功整理：{summary['succeeded_count']} 条\n"
                f"未成功或未确认：{summary['unresolved_count']} 条\n"
                f"方案排除：{summary['excluded_count']} 条\n\n"
                "执行入口未返回未成功项的逐条原因，请核对日志和源/目标路径。"
                "本结果仅为本次会话摘要，不保留逐项历史，也不承诺对原始素材全部恢复。",
            )

        def on_fail(msg):
            self._busy_io_end()
            self.load_data()
            QMessageBox.warning(self, "整理失败", msg or "未知错误")

        run_io_job(
            self, fn=job, on_ok=on_ok, on_fail=on_fail,
            on_start=lambda: self._busy_io_start("正在物理整理…"),
            busy_message="正在物理整理…",
        )

    def on_task_finished(self, success, message):
        self.set_ui_enabled(True)
        self.cancel_analysis_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.sub_progress_bar.setVisible(False)
        self.progress_updated.emit(-1)
        self.progress_bar.setRange(0, 100)
        self.sub_progress_bar.setRange(0, 100)
        self.status_label.setText(message)
        if hasattr(self.model, "clear_temp_status_map"):
            self.model.clear_temp_status_map()
        # 强制丢弃 L1 缓存并从 DB 重载，避免分析中零散写入导致 status 仍显示 failed
        if "模拟" not in (message or ""):
            try:
                self.service.invalidate_video_memory_cache()
            except Exception:
                pass
            self.load_data()
        if success:
            QMessageBox.information(self, "任务完成", message)
        else:
            QMessageBox.critical(self, "错误", message)