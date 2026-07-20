# -*- coding: utf-8 -*-
import os
import sys
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QSplitter, QStackedWidget, QTableView, QListView,
    QHeaderView, QAbstractItemView, QProgressBar, QMessageBox, QMenu,
    QDialog, QFileDialog, QTreeView, QListView as QtListView,
)
from PySide6.QtCore import Qt, Signal, QItemSelectionModel
from ..widgets.filter_panel import FilterPanel
from ..widgets.detail_panel import DetailPanel
from ..widgets.delegates import CardDelegate, StatusDelegate, ThumbnailDelegate
from ..models.video_table import VideoTableModel
from ..models.proxy_model import AdvancedSortFilterProxyModel
from ..workers.analysis_worker import AnalysisWorker
from ..workers.rename_worker import RenameWorker
from ..widgets.rename_dialog import BatchRenameDialog
from core.video_organizer_service import VideoOrganizerService
from gui.styles import normalize_theme


class WorkstationView(QWidget):
    """工作台视图 - 核心视频处理区域（仅呈现当前工作范围）"""
    video_selected = Signal(dict) # 选中视频信号
    status_message = Signal(str)  # 状态栏消息信号
    progress_updated = Signal(int) # 进度值信号

    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = service.settings
        self.worker = None
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

        # 筛选器开关
        self.filter_btn = QPushButton("高级筛选")
        self.filter_btn.setCheckable(True)
        self.filter_btn.setChecked(False)
        self.filter_btn.clicked.connect(self.toggle_filter_panel)

        # 视图切换
        self.view_switch_btn = QPushButton("切换视图")
        self.view_switch_btn.setCheckable(True)
        self.view_switch_btn.clicked.connect(self.toggle_view_mode)
        self.view_switch_btn.setToolTip("切换列表/网格视图")

        self.analyze_btn = QPushButton("开始分析")
        self.analyze_btn.setObjectName("primary_button")
        self.analyze_btn.setStyleSheet("background-color: #3d5afe; color: white; font-weight: bold;")
        self.analyze_btn.clicked.connect(self.start_analysis)
        
        toolbar.addWidget(QLabel("路径:"))
        toolbar.addWidget(self.path_input)
        toolbar.addWidget(browse_btn)
        toolbar.addWidget(accumulate_btn)
        toolbar.addSpacing(20)
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.filter_btn)
        toolbar.addWidget(self.view_switch_btn)
        toolbar.addWidget(self.analyze_btn)
        
        layout.addLayout(toolbar)

        # 2. 筛选面板
        self.filter_panel = FilterPanel(self.settings)
        self.filter_panel.setVisible(False)
        self.filter_panel.filterChanged.connect(self.apply_advanced_filter)
        layout.addWidget(self.filter_panel)

        # 3. 中间区域
        self.splitter = QSplitter(Qt.Horizontal)
        self.view_stack = QStackedWidget()
        
        # --- 表格视图 ---
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
        self.table_view.setSortingEnabled(True)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.verticalHeader().setDefaultSectionSize(80) # 设置默认行高以适应 16:9 缩略图
        
        # 配置列头
        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_view.setColumnWidth(0, 50)
        self.table_view.setColumnWidth(1, 140) # 稍微增加宽度以适应 16:9 比例
        self.table_view.setColumnWidth(3, 100)
        self.table_view.setColumnWidth(4, 150)
        self.table_view.setColumnWidth(5, 80)

        self.thumb_delegate = ThumbnailDelegate()
        self.status_delegate = StatusDelegate()
        self.table_view.setItemDelegateForColumn(1, self.thumb_delegate)
        self.table_view.setItemDelegateForColumn(5, self.status_delegate)

        self.table_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self.show_context_menu)

        table_layout.addWidget(self.table_view)
        
        # --- 卡片视图 ---
        self.card_view = QListView()
        self.card_view.setViewMode(QListView.IconMode)
        self.card_view.setResizeMode(QListView.Adjust)
        self.card_view.setUniformItemSizes(True)
        self.card_view.setWordWrap(True)
        self.card_view.setSpacing(10)
        self.card_view.setModel(self.proxy_model)
        self.card_view.setModelColumn(2)
        self.card_view.setItemDelegate(CardDelegate(self.card_view))
        self.card_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.card_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.card_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.card_view.customContextMenuRequested.connect(self.show_context_menu)
        
        self.view_stack.addWidget(table_container)
        self.view_stack.addWidget(self.card_view)

        # 右侧详情面板
        self.detail_panel = DetailPanel(self.service)
        self.detail_panel.data_changed.connect(self.load_data)
        
        self.splitter.addWidget(self.view_stack)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        
        layout.addWidget(self.splitter)

        # 4. 底部控制栏
        controls_layout = QHBoxLayout()
        self.simulate_btn = QPushButton("模拟重命名")
        self.apply_btn = QPushButton("应用重命名")
        self.rollback_btn = QPushButton("回滚")
        self.export_fcpx_btn = QPushButton("导出 FCPX")
        self.export_ale_btn = QPushButton("导出 ALE (达芬奇)")
        self.sync_xmp_btn = QPushButton("同步元数据 (XMP)")
        self.sync_xmp_btn.setStyleSheet("background-color: #2e7d32; color: white;")
        
        # V4.0: 新增自动化按钮
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

        # 进度条
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status_label = QLabel("准备就绪")
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.progress_bar, 1)
        layout.addLayout(progress_layout)

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
            # 同步选择到卡片视图
            self.card_view.selectionModel().clearSelection()
            for idx in self.table_view.selectionModel().selectedRows():
                self.card_view.selectionModel().select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            
            self.view_stack.setCurrentWidget(self.card_view)
            self.view_switch_btn.setText("切换列表")
        else:
            # 同步选择到表格视图
            self.table_view.selectionModel().clearSelection()
            for idx in self.card_view.selectionModel().selectedIndexes():
                self.table_view.selectionModel().select(idx, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                
            self.view_stack.setCurrentIndex(0)
            self.view_switch_btn.setText("切换视图")

    def filter_text_changed(self, text):
        self.proxy_model.set_filter_params({"text": text})

    def apply_advanced_filter(self, params):
        self.proxy_model.set_filter_params(params)

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
                    # 回退：相对名不可靠，跳过
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

    def browse_path(self):
        """多选文件与文件夹，确认后整份替换工作范围并扫盘入库。"""
        dialog = QFileDialog(self, "选择工作范围（可多选文件与文件夹，确认后整份替换）")
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        # Directory 模式 + 显示文件：便于一次多选文件夹；文件亦可选
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

        result = self.service.replace_work_scope(selected, scan=True)
        self._refresh_scope_path_ui()
        self.load_data()
        n_paths = len(result.get("paths") or [])
        n_reg = len(result.get("registered") or [])
        self.status_label.setText(
            f"工作范围已替换为 {n_paths} 项；新入库未分析 {n_reg} 个"
        )
        self.status_message.emit(self.status_label.text())

    def accumulate_path(self):
        """多选路径并累加进当前工作范围。"""
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

        result = self.service.append_work_scope(selected, scan=True)
        self._refresh_scope_path_ui()
        self.load_data()
        n_paths = len(result.get("paths") or [])
        n_reg = len(result.get("registered") or [])
        self.status_label.setText(
            f"工作范围已累加，现共 {n_paths} 项；新入库未分析 {n_reg} 个"
        )
        self.status_message.emit(self.status_label.text())

    def load_data(self):
        """从服务层加载「工作范围内」的已入库视频；空范围显示空列表与提示。"""
        scope = self.service.get_work_scope_paths()
        videos = self.service.get_videos_in_work_scope()
        self.model.update_data(videos)
        self._refresh_scope_path_ui()
        if not scope:
            self.status_label.setText("请先设定工作范围：点击「浏览」选择文件或文件夹")
            self.status_message.emit(self.status_label.text())
        else:
            self.status_label.setText(f"工作范围内共 {len(videos)} 个已入库视频")

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
        elif len(rows) == 1:
            proxy_idx = self.proxy_model.index(rows[0], 0)
            source_idx = self.proxy_model.mapToSource(proxy_idx)
            video_data = self.model.videos[source_idx.row()]
            self.video_selected.emit(video_data)
            self.detail_panel.load_video_data(video_data)

    def show_context_menu(self, pos):
        sender = self.sender()
        if not sender: return
        
        if sender == self.card_view:
            indexes = self.card_view.selectionModel().selectedIndexes()
        else:
            indexes = self.table_view.selectionModel().selectedRows()
            
        if not indexes: return

        menu = QMenu(self)
        analyze_action = menu.addAction("🔍 分析选中项")
        delete_action = menu.addAction("🗑️ 删除记录")
        menu.addSeparator()
        replace_tag_action = menu.addAction("🔄 批量替换标签")
        menu.addSeparator()
        folder_action = menu.addAction("📂 打开所在文件夹")
        play_action = menu.addAction("▶️ 播放视频")
        
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
        """弹出对话框进行批量标签替换"""
        from PySide6.QtWidgets import QInputDialog
        old_tag, ok1 = QInputDialog.getText(self, "批量替换标签", "请输入要替换的原标签:")
        if not ok1 or not old_tag: return
        
        new_tag, ok2 = QInputDialog.getText(self, "批量替换标签", f"将 '{old_tag}' 替换为:")
        if not ok2: return
        
        reply = QMessageBox.question(self, "确认替换", f"确定要将所有视频中的标签 '{old_tag}' 替换为 '{new_tag}' 吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.service.bulk_replace_tags(old_tag, new_tag)
            self.load_data()
            QMessageBox.information(self, "成功", "批量替换完成。")

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
        source_rows = self.get_selected_source_rows()
        paths = [self.model.videos[row].get("path") for row in source_rows]
        if not paths: return
        
        self.set_ui_enabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        
        self.worker = AnalysisWorker(self.service, paths)
        self.worker.progress_updated.connect(self.update_status)
        self.worker.task_finished.connect(self.on_task_finished)
        self.worker.start()

    def delete_selected(self):
        source_rows = self.get_selected_source_rows()
        if not source_rows: return
        
        reply = QMessageBox.question(self, "确认删除", f"确定要从数据库中删除选中的 {len(source_rows)} 条记录吗？\n(物理文件不会被删除)",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            paths = [self.model.videos[row].get("path") for row in source_rows]
            self.service.delete_videos(paths)
            self.load_data()

    def open_selected_folder(self):
        source_rows = self.get_selected_source_rows()
        if not source_rows: return
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

    def set_ui_enabled(self, enabled):
        self.analyze_btn.setEnabled(enabled)
        self.simulate_btn.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        self.rollback_btn.setEnabled(enabled)
        self.sync_xmp_btn.setEnabled(enabled)
        self.path_input.setEnabled(enabled)

    def start_analysis(self):
        """分析当前工作范围内的视频（有勾选则仅勾选；未勾选则范围全部）。"""
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先通过「浏览」设定工作范围。")
            return

        paths = self.service.resolve_operation_target_paths(list(self.model.checked_items) or None)
        if not paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可分析的视频。")
            return

        self.set_ui_enabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        self.worker = AnalysisWorker(self.service, paths)
        self.worker.progress_updated.connect(self.update_status)
        self.worker.task_finished.connect(self.on_task_finished)
        self.worker.start()

    def start_rename(self, dry_run=True):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return

        checked = list(self.model.checked_items) or None
        selected_paths = self.service.resolve_operation_target_paths(checked)
        selected_videos = self.service.get_videos_for_operation(checked)

        if not selected_videos:
            QMessageBox.warning(self, "警告", "工作范围内没有可重命名的视频。")
            return

        dialog = BatchRenameDialog(self.service, selected_videos, self)
        if dialog.exec() != QDialog.Accepted:
            return
            
        config = dialog.get_final_config()

        if not dry_run:
            reply = QMessageBox.question(self, "确认", "确定要应用重命名吗？文件将被物理重命名。", 
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No: return

        self.set_ui_enabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        
        self.worker = RenameWorker(
            self.service, 
            dry_run=dry_run, 
            selected_paths=selected_paths,
            pattern=config["pattern"],
            regex_find=config["find_regex"],
            regex_replace=config["replace_str"]
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

    def export_fcpx(self):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        selected_paths = self.service.resolve_operation_target_paths(
            list(self.model.checked_items) or None
        )
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可导出的视频。")
            return
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 FCPX XML", "", "FCPXML Files (*.fcpxml)")
        if file_path:
            self.service.export_to_fcpx_xml(file_path, selected_paths)
            QMessageBox.information(self, "成功", f"FCPX XML 已导出至: {file_path}")

    def export_ale(self):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        selected_paths = self.service.resolve_operation_target_paths(
            list(self.model.checked_items) or None
        )
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可导出的视频。")
            return
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 ALE (达芬奇)", "", "ALE Files (*.ale)")
        if file_path:
            success = self.service.export_to_ale(file_path, selected_paths)
            if success:
                QMessageBox.information(self, "成功", f"ALE 文件已导出至: {file_path}\n请在达芬奇中使用 'File -> Import -> Metadata from ALE...' 导入。")
            else:
                QMessageBox.warning(self, "失败", "导出 ALE 文件失败。")

    def sync_metadata(self):
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        selected_paths = self.service.resolve_operation_target_paths(
            list(self.model.checked_items) or None
        )
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可同步的视频。")
            return
            
        count = self.service.sync_metadata_to_xmp(selected_paths)
        QMessageBox.information(self, "完成", f"已成功为 {count} 个视频生成 XMP 侧边文件。")

    def start_auto_organize(self):
        """V4.0: 触发物理迁移自动整理"""
        scope = self.service.get_work_scope_paths()
        if not scope:
            QMessageBox.warning(self, "警告", "请先设定工作范围。")
            return
        selected_paths = self.service.resolve_operation_target_paths(
            list(self.model.checked_items) or None
        )
        if not selected_paths:
            QMessageBox.warning(self, "警告", "工作范围内没有可整理的视频。")
            return
            
        target_root = QFileDialog.getExistingDirectory(self, "选择整理后的目标根目录")
        if not target_root:
            return
            
        reply = QMessageBox.question(self, "确认整理", f"确定要将 {len(selected_paths)} 个文件移动到分类目录吗？\n目标: {target_root}",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            count = self.service.execute_physical_migration(target_root, selected_paths)
            QMessageBox.information(self, "完成", f"已成功整理 {count} 个文件。")
            self.load_data()

    def on_task_finished(self, success, message):
        self.set_ui_enabled(True)
        self.progress_bar.setVisible(False)
        self.progress_updated.emit(-1)
        self.progress_bar.setRange(0, 100)
        self.status_label.setText(message)
        if success:
            if "模拟" not in message:
                self.load_data()
            QMessageBox.information(self, "任务完成", message)
        else:
            QMessageBox.critical(self, "错误", message)
