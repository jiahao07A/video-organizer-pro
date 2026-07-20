# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox, QMenu,
    QFileDialog, QProgressDialog, QListView, QScrollArea, QFrame,
    QSplitter, QDialog
)
from PySide6.QtGui import QColor, QAction, QIcon, QDragEnterEvent, QDropEvent, QPalette
from PySide6.QtCore import Qt, QSize, Slot
import json
from core.video_organizer_service import VideoOrganizerService, SettingsManager
from core.tag_import_service import TagImportService
from gui.models.tag_model import TagListModel, TagFilterProxyModel
from gui.widgets.delegates import TagChipDelegate
from gui.widgets.import_wizard import TagImportWizard
from gui.views.tags_library_editors import TagGroupEditor, PromptConfigCenter
from gui.styles import normalize_theme, get_theme_colors


def tags_library_palette(theme: str = "dark") -> dict:
    """兼容旧名：与全局 get_theme_colors 同一色板。"""
    return get_theme_colors(theme)


class TagHeatmapWidget(QFrame):
    """标签热力图仪表盘"""
    def __init__(self, model: TagListModel, colors: dict = None, parent=None):
        super().__init__(parent)
        self.model = model
        self.colors = colors or tags_library_palette("dark")
        self.setFixedHeight(100)
        self._apply_frame_style()
        self.setup_ui()

    def _apply_frame_style(self):
        c = self.colors
        self.setStyleSheet(f"""
            TagHeatmapWidget {{
                background-color: {c["heatmap_bg"]};
                border-radius: 10px;
                border: 1px solid {c["heatmap_border"]};
            }}
        """)
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        
        header = QHBoxLayout()
        self.title_label = QLabel("标签热力图看板 (Top 10)")
        self.title_label.setStyleSheet(
            f"font-weight: bold; color: {self.colors['heatmap_title']}; font-size: 14px;"
        )
        header.addWidget(self.title_label)
        header.addStretch()
        layout.addLayout(header)
        
        self.content_layout = QHBoxLayout()
        self.content_layout.setSpacing(10)
        layout.addLayout(self.content_layout)
        
        self.update_data()

    def set_colors(self, colors: dict):
        self.colors = colors
        self._apply_frame_style()
        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet(
                f"font-weight: bold; color: {self.colors['heatmap_title']}; font-size: 14px;"
            )
        self.update_data()

    def update_data(self):
        # 清除现有
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # 获取 top 10
        all_tags = []
        for i in range(self.model.rowCount()):
            all_tags.append(self.model.get_tag(i))
        
        sorted_tags = sorted([t for t in all_tags if t.usage_count > 0], 
                            key=lambda x: x.usage_count, reverse=True)[:10]
        
        if not sorted_tags:
            empty = QLabel("暂无使用数据")
            empty.setStyleSheet(f"color: {self.colors['muted']}; font-style: italic;")
            self.content_layout.addWidget(empty)
            return

        max_count = sorted_tags[0].usage_count
        
        for tag in sorted_tags:
            tag_block = QWidget()
            v_layout = QVBoxLayout(tag_block)
            v_layout.setContentsMargins(0, 0, 0, 0)
            v_layout.setSpacing(2)
            
            # 计算颜色深浅 (热力图)
            alpha = max(50, int((tag.usage_count / max_count) * 255))
            color = f"rgba(255, 179, 0, {alpha/255.0})" # 琥珀金
            
            bar = QFrame()
            bar.setFixedHeight(30)
            bar.setMinimumWidth(60)
            bar.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
            bar.setToolTip(f"{tag.name}: {tag.usage_count} 次")
            
            name_label = QLabel(tag.name)
            name_label.setAlignment(Qt.AlignCenter)
            name_label.setStyleSheet(f"font-size: 11px; color: {self.colors['input_text']};")
            
            count_label = QLabel(str(tag.usage_count))
            count_label.setAlignment(Qt.AlignCenter)
            count_label.setStyleSheet(f"font-size: 10px; color: {self.colors['muted']}; font-weight: bold;")
            
            v_layout.addWidget(bar)
            v_layout.addWidget(name_label)
            v_layout.addWidget(count_label)
            
            self.content_layout.addWidget(tag_block)
        
        self.content_layout.addStretch()

class TagsView(QWidget):
    """下一代标签库管理 - 实现 Model/View 基础架构与 6 分栏布局"""
    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.tag_config = service.tag_config
        self.import_service = TagImportService(service.ai)
        self.ui_colors = self._resolve_colors()
        
        # 初始化模型
        self.model = TagListModel(self)
        self.column_proxies = {}
        
        self.setup_ui()
        self.load_data()

    def _resolve_colors(self) -> dict:
        theme = SettingsManager.get_setting(self.service.settings, "ui_preferences.theme", "dark")
        return get_theme_colors(theme)

    def apply_theme(self, theme=None):
        """主题切换后即时重涂标签库（无需重建页面）。"""
        if theme is None:
            theme = SettingsManager.get_setting(self.service.settings, "ui_preferences.theme", "dark")
        theme = normalize_theme(theme)
        self.ui_colors = get_theme_colors(theme)
        c = self.ui_colors

        if hasattr(self, "title_label"):
            self.title_label.setStyleSheet(
                f"font-size: 24px; font-weight: bold; color: {c['title']};"
            )
        if hasattr(self, "heatmap_widget"):
            self.heatmap_widget.set_colors(c)
        if hasattr(self, "splitter"):
            self.splitter.setStyleSheet(
                f"QSplitter::handle {{ background-color: {c['splitter']}; }}"
            )
            # 重建分栏以刷新列表 palette / 输入框 QSS
            self.refresh_columns()

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        self.main_layout.setSpacing(15)
        c = self.ui_colors

        # Header
        header_layout = QHBoxLayout()
        self.title_label = QLabel("标签库管理 V2")
        self.title_label.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {c['title']};")
        
        import_btn = QPushButton("导入标签")
        import_btn.clicked.connect(self.import_from_txt)
        
        group_edit_btn = QPushButton("管理标签组")
        group_edit_btn.clicked.connect(self.open_group_editor)
        
        prompt_config_btn = QPushButton("Prompt 配置")
        prompt_config_btn.clicked.connect(self.open_prompt_config)
        
        save_btn = QPushButton("保存所有更改")
        save_btn.setObjectName("primary_button")
        save_btn.clicked.connect(self.save_config)
        
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(group_edit_btn)
        header_layout.addWidget(prompt_config_btn)
        header_layout.addWidget(import_btn)
        header_layout.addWidget(save_btn)
        self.main_layout.addLayout(header_layout)

        # 标签热力图看板
        self.heatmap_widget = TagHeatmapWidget(self.model, self.ui_colors)
        self.main_layout.addWidget(self.heatmap_widget)

        # 6 分栏布局容器
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        
        container = QWidget()
        main_h_layout = QHBoxLayout(container)
        main_h_layout.setContentsMargins(0, 0, 0, 0)
        
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(2)
        self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {c['splitter']}; }}")

        # 动态分栏布局
        self.refresh_columns()

        main_h_layout.addWidget(self.splitter)
        scroll_area.setWidget(container)
        self.main_layout.addWidget(scroll_area)

    def create_column(self, dim_id, dim_name, color):
        c = self.ui_colors
        col_widget = QFrame()
        col_widget.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        col_widget.setMinimumWidth(260)
        col_widget.setStyleSheet(f"""
            QFrame {{
                background-color: {c["panel_bg"]};
                border-radius: 8px;
                border: 1px solid {c["panel_border"]};
            }}
        """)
        
        layout = QVBoxLayout(col_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Title + Config Icon
        header = QHBoxLayout()
        title_label = QLabel(dim_name)
        title_label.setStyleSheet(f"font-weight: bold; color: {color}; border: none; font-size: 14px;")
        
        config_btn = QPushButton("⚙")
        config_btn.setFixedSize(24, 24)
        config_btn.setStyleSheet(f"border: none; color: {c['muted']}; font-size: 16px;")
        config_btn.setToolTip("配置属性")
        config_btn.clicked.connect(lambda _, d=dim_id, b=config_btn: self.show_dim_config(d, b))
        
        header.addWidget(title_label)
        header.addStretch()
        if dim_id != "pool":
            header.addWidget(config_btn)
        layout.addLayout(header)

        # Search Bar
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("过滤标签...")
        search_edit.setStyleSheet(
            f"background-color: {c['input_bg']}; color: {c['input_text']}; "
            f"border: 1px solid {c['panel_border']}; border-radius: 4px; height: 24px; padding: 2px 6px;"
        )
        layout.addWidget(search_edit)

        # List View
        list_view = QListView()
        list_view.setFrameShape(QFrame.NoFrame)
        list_view.setSpacing(4)
        list_view.setSelectionMode(QListView.ExtendedSelection)
        list_view.setDragEnabled(True)
        list_view.setAcceptDrops(True)
        list_view.setDefaultDropAction(Qt.MoveAction)
        list_view.setViewMode(QListView.IconMode) # 使用 IconMode 以支持流式排列
        list_view.setFlow(QListView.LeftToRight)
        list_view.setWrapping(True)
        list_view.setResizeMode(QListView.Adjust)
        list_view.setWordWrap(True)
        list_view.setMovement(QListView.Static) # 禁用视图内的自由拖动，仅允许跨列拖拽
        list_view.setStyleSheet(
            f"QListView {{ background-color: {c['list_bg']}; border: none; color: {c['chip_text']}; }}"
        )
        # 让 TagChipDelegate 从 palette 取到与分栏一致的可读颜色
        pal = list_view.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(c["chip_bg"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(c["chip_text"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(c["list_bg"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(c["chip_text"]))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        pal.setColor(QPalette.ColorRole.Mid, QColor(c["chip_border"]))
        list_view.setPalette(pal)
        list_view.setAutoFillBackground(True)
        
        # 启用右键菜单
        list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        list_view.customContextMenuRequested.connect(lambda pos, lv=list_view, d=dim_id: self.show_tag_context_menu(pos, lv, d))
        
        # 快捷键支持
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence(Qt.Key_Delete), list_view).activated.connect(lambda: self.batch_delete_tags(list_view))
        QShortcut(QKeySequence("Ctrl+A"), list_view).activated.connect(list_view.selectAll)
        
        # 覆写事件以处理跨列拖放
        list_view.dragEnterEvent = self.on_drag_enter
        list_view.dragMoveEvent = lambda e: e.accept()
        list_view.dropEvent = lambda e, d=dim_id: self.on_drop(e, d)

        # Proxy Model
        proxy = TagFilterProxyModel(dim_id, self)
        proxy.setSourceModel(self.model)
        list_view.setModel(proxy)
        self.column_proxies[dim_id] = proxy
        
        # Delegate
        delegate = TagChipDelegate(list_view)
        list_view.setItemDelegate(delegate)
        
        search_edit.textChanged.connect(proxy.set_filter_text)
        
        layout.addWidget(list_view)

        # Footer (Quick Add)
        footer = QHBoxLayout()
        quick_add = QLineEdit()
        quick_add.setPlaceholderText("+ 快速添加...")
        quick_add.setStyleSheet(
            f"background-color: {c['input_bg']}; color: {c['input_text']}; "
            f"border: 1px solid {c['panel_border']}; border-radius: 4px; height: 24px; padding: 2px 6px;"
        )
        quick_add.returnPressed.connect(lambda d=dim_id, q=quick_add: self.add_tag_to_dim(d, q))
        
        footer.addWidget(quick_add)
        layout.addLayout(footer)

        return col_widget

    def refresh_columns(self):
        """根据配置动态生成分栏"""
        # 清除现有列
        while self.splitter.count() > 0:
            widget = self.splitter.widget(0)
            widget.setParent(None)
            widget.deleteLater()
        
        self.column_proxies = {}
        
        # 预定义颜色方案
        colors = ["#FADB14", "#1890FF", "#52C41A", "#722ED1", "#EB2F96", "#FA8C16", "#13C2C2", "#722ED1"]
        # light 主题下部分亮色标题对比不足时加深
        theme = normalize_theme(
            SettingsManager.get_setting(self.service.settings, "ui_preferences.theme", "dark")
        )
        if theme == "light":
            colors = ["#ad8b00", "#096dd9", "#389e0d", "#531dab", "#c41d7f", "#d46b08", "#08979c", "#531dab"]
        
        # 1. 中转池
        pool_column = self.create_column("pool", "中转池 (All)", self.ui_colors["muted"])
        self.splitter.addWidget(pool_column)
        
        # 2. 动态组
        tag_groups = self.tag_config.get("tag_groups", [])
        for i, group in enumerate(tag_groups):
            color = colors[i % len(colors)]
            column = self.create_column(group["id"], group["name"], color)
            self.splitter.addWidget(column)

    def load_data(self):
        """从数据库加载标签并根据配置分类"""
        # 1. 整理所有在配置中的标签及其归属组
        config_tags = {}
        for group in self.tag_config.get("tag_groups", []):
            group_id = group["id"]
            for tag_name in group.get("tags", []):
                config_tags[tag_name] = group_id
        
        # 2. 从数据库加载详情
        tags_detail = self.service.db.get_tags_detail()
        formatted_data = []
        processed_names = set()
        
        # 先处理配置中明确指定的标签
        for tag_name, group_id in config_tags.items():
            detail = next((t for t in tags_detail if t["tag_name"] == tag_name), None)
            if detail:
                formatted_data.append({
                    "id": detail["id"],
                    "tag_name": tag_name,
                    "dimension": group_id,
                    "usage_count": detail["usage_count"],
                    "color": detail["color"],
                    "parent_id": detail.get("parent_id")
                })
            else:
                formatted_data.append({
                    "id": -1,
                    "tag_name": tag_name,
                    "dimension": group_id,
                    "usage_count": 0,
                    "color": None,
                    "parent_id": None
                })
            processed_names.add(tag_name)
            
        # 其余标签放入中转池
        for t in tags_detail:
            if t["tag_name"] not in processed_names:
                formatted_data.append({
                    "id": t["id"],
                    "tag_name": t["tag_name"],
                    "dimension": "pool",
                    "usage_count": t["usage_count"],
                    "color": t["color"],
                    "parent_id": t.get("parent_id")
                })
                
        self.model.set_tags(formatted_data)
        
        # 3. 异步刷新频次统计
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, self.async_refresh_usage_counts)

    def async_refresh_usage_counts(self):
        """异步刷新统计数据以提高性能"""
        import threading
        def run():
            self.service.db.refresh_tag_usage_counts()
            from PySide6.QtCore import QMetaObject, Qt
            QMetaObject.invokeMethod(self, "load_data_silent", Qt.QueuedConnection)
            
        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def load_data_silent(self):
        """悄悄更新模型数据而不重置滚动位置（部分更新）"""
        tags_detail = self.service.db.get_tags_detail()
        detail_map = {t["tag_name"]: t for t in tags_detail}
        
        for i in range(self.model.rowCount()):
            tag = self.model.get_tag(i)
            if tag.name in detail_map:
                detail = detail_map[tag.name]
                if tag.usage_count != detail["usage_count"]:
                    tag.usage_count = detail["usage_count"]
                    idx = self.model.index(i)
                    self.model.dataChanged.emit(idx, idx, [TagListModel.USAGE_ROLE, Qt.DisplayRole])
        
        # 更新热力图（如果存在）
        if hasattr(self, "heatmap_widget"):
            self.heatmap_widget.update_data()

    def open_group_editor(self):
        from gui.views.tags_library_editors import TagGroupEditor
        dialog = TagGroupEditor(self.tag_config, self)
        if dialog.exec() == QDialog.Accepted:
            self.save_config(silent=True)
            self.refresh_columns()
            self.load_data()

    def open_prompt_config(self):
        from gui.views.tags_library_editors import PromptConfigCenter
        dialog = PromptConfigCenter(self.tag_config, self)
        if dialog.exec() == QDialog.Accepted:
            self.save_config()

    def add_tag_to_dim(self, dim_id, line_edit):
        name = line_edit.text().strip()
        if not name:
            return
            
        # 检查是否已存在
        for i in range(self.model.rowCount()):
            tag = self.model.get_tag(i)
            if tag.name == name:
                tag.dimension = dim_id
                self.model.dataChanged.emit(self.model.index(i), self.model.index(i))
                line_edit.clear()
                return

        # 添加新标签
        self.model.add_tag({
            "id": -1,
            "tag_name": name,
            "dimension": dim_id,
            "usage_count": 0
        })
        line_edit.clear()

    def show_dim_config(self, group_id, button=None):
        """显示标签组配置菜单"""
        group = next((g for g in self.tag_config.get("tag_groups", []) if g["id"] == group_id), None)
        if not group:
            return

        rules = group.get("rules", {})
        menu = QMenu(self)
        
        # Selection Mode
        mode_menu = menu.addMenu(f"选择模式: {rules.get('selection_mode', 'single')}")
        for mode in ["single", "multiple"]:
            act = mode_menu.addAction(mode)
            act.triggered.connect(lambda checked=False, g=group_id, v=mode: self.update_group_rule(g, "selection_mode", v))
        
        # Max Count
        count_menu = menu.addMenu(f"最大数量: {rules.get('max_count', 1)}")
        for i in [1, 2, 3, 5, 10]:
            act = count_menu.addAction(str(i))
            act.triggered.connect(lambda checked=False, g=group_id, v=i: self.update_group_rule(g, "max_count", v))
        
        # AI Expandable
        expand_action = menu.addAction("允许 AI 扩展标签库")
        expand_action.setCheckable(True)
        expand_action.setChecked(rules.get("ai_expandable", False))
        expand_action.triggered.connect(lambda checked, g=group_id: self.update_group_rule(g, "ai_expandable", checked))
        
        # Local Prompt
        prompt_action = menu.addAction("编辑局部 Prompt...")
        prompt_action.triggered.connect(lambda: self.edit_group_prompt(group_id))
        
        # 弹出菜单
        if button:
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        else:
            from PySide6.QtGui import QCursor
            menu.exec(QCursor.pos())

    def update_group_rule(self, group_id, key, value):
        for group in self.tag_config.get("tag_groups", []):
            if group["id"] == group_id:
                group.setdefault("rules", {})[key] = value
                self.save_config(silent=True)
                break

    def edit_group_prompt(self, group_id):
        group = next((g for g in self.tag_config.get("tag_groups", []) if g["id"] == group_id), None)
        if not group: return
        
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(self, "编辑局部 Prompt", f"组 '{group['name']}' 的 AI 引导词:",
                                               group.get("rules", {}).get("local_prompt", ""))
        if ok:
            group.setdefault("rules", {})["local_prompt"] = text
            self.save_config(silent=True)

    def on_drag_enter(self, event: QDragEnterEvent):
        if event.mimeData().hasFormat("application/x-tag-data"):
            event.acceptProposedAction()

    def on_drop(self, event: QDropEvent, target_dim: str):
        if event.mimeData().hasFormat("application/x-tag-data"):
            try:
                data = json.loads(event.mimeData().data("application/x-tag-data").data().decode("utf-8"))
                source_rows = [item["source_row"] for item in data]
                self.model.move_tags_to_dimension(source_rows, target_dim)
                self.save_config(silent=True) # 实时响应数据库
                event.acceptProposedAction()
            except Exception as e:
                print(f"Drop failed: {e}")

    def show_tag_context_menu(self, pos, list_view: QListView, current_dim: str):
        selected_indexes = list_view.selectedIndexes()
        if not selected_indexes:
            return

        menu = QMenu(self)
        
        # 批量操作
        menu.addAction("全选", list_view.selectAll)
        menu.addAction("取消全选", list_view.clearSelection)
        menu.addSeparator()

        # 批量移动
        move_menu = menu.addMenu("批量移动到")
        
        move_targets = [("pool", "中转池")]
        for group in self.tag_config.get("tag_groups", []):
            move_targets.append((group["id"], group["name"]))
        
        # 获取源行（在 model 中的原始行）
        source_rows = [idx.model().mapToSource(idx).row() for idx in selected_indexes]

        for dim_id, dim_name in move_targets:
            if dim_id == current_dim:
                continue
            act = move_menu.addAction(dim_name)
            act.triggered.connect(lambda checked=False, d=dim_id, rows=source_rows:
                                 self.move_tags_and_save(rows, d))

        menu.addSeparator()
        
        # 标签合并
        if len(selected_indexes) > 1:
            merge_act = menu.addAction("合并选中标签")
            merge_act.triggered.connect(lambda: self.merge_selected_tags(list_view))
            menu.addSeparator()

        # 层级管理
        if len(selected_indexes) == 1:
            parent_menu = menu.addMenu("设置父标签")
            # 列出除自己以外的所有标签作为候选父标签
            current_tag = self.model.get_tag(source_rows[0])
            candidates = []
            for i in range(self.model.rowCount()):
                t = self.model.get_tag(i)
                if t.id != current_tag.id and t.name != current_tag.name:
                    candidates.append(t)
            
            none_act = parent_menu.addAction("无 (顶级)")
            none_act.triggered.connect(lambda: self.set_tag_parent(current_tag, None))
            
            for c in sorted(candidates, key=lambda x: x.name)[:20]: # 限制数量防止菜单太长
                act = parent_menu.addAction(c.name)
                act.triggered.connect(lambda checked=False, tag=current_tag, p_id=c.id: self.set_tag_parent(tag, p_id))

            # 同义词管理
            synonym_act = menu.addAction("管理同义词 (别名)...")
            synonym_act.triggered.connect(lambda: self.manage_tag_synonyms(current_tag))
            
            # 人脸识别联动：标记为人物 (V6.0)
            person_act = menu.addAction("👤 标记为'人物' (用于人脸联动)")
            person_act.setCheckable(True)
            person_act.setChecked(current_tag.is_person)
            person_act.triggered.connect(lambda checked: self.toggle_tag_person_status(current_tag, checked))
            
            menu.addSeparator()

        # 批量删除
        delete_act = menu.addAction("批量删除")
        delete_act.setStyleSheet("color: #FF4D4F;")
        delete_act.triggered.connect(lambda: self.batch_delete_tags(list_view))

        menu.exec(list_view.mapToGlobal(pos))

    def move_tags_and_save(self, rows, target_dim):
        self.model.move_tags_to_dimension(rows, target_dim)
        self.save_config(silent=True)

    def merge_selected_tags(self, list_view: QListView):
        selected_indexes = list_view.selectedIndexes()
        if len(selected_indexes) < 2:
            return
            
        first_tag_name = selected_indexes[0].data(Qt.DisplayRole)
        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, "合并标签", "请输入合并后的标签名称:", 
                                           QLineEdit.Normal, first_tag_name)
        
        if ok and new_name.strip():
            source_rows = [idx.model().mapToSource(idx).row() for idx in selected_indexes]
            self.model.merge_tags(source_rows, new_name.strip())

    def batch_delete_tags(self, list_view: QListView):
        selected_indexes = list_view.selectedIndexes()
        if not selected_indexes:
            return
            
        if QMessageBox.question(self, "确认删除", f"确定要删除选中的 {len(selected_indexes)} 个标签吗？",
                               QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
            return

        # 必须从后往前删
        source_rows = sorted([idx.model().mapToSource(idx).row() for idx in selected_indexes], reverse=True)
        for row in source_rows:
            self.model.remove_tag(row)

    def import_from_txt(self):
        from PySide6.QtWidgets import QWizard
        wizard = TagImportWizard(self.import_service, self)
        if wizard.exec() == QWizard.Accepted:
            # 获取向导最终结果并入库
            results = getattr(wizard, "final_classified", {})
            for cat_id, tags in results.items():
                for tag_name in tags:
                    self.add_tag_to_dim_if_new(cat_id, tag_name)
            
            QMessageBox.information(self, "导入完成", "标签已成功导入并分类。")

    def add_tag_to_pool_if_new(self, name):
        # 检查是否已在模型中
        for i in range(self.model.rowCount()):
            if self.model.get_tag(i).name == name:
                return
        self.model.add_tag({"tag_name": name, "dimension": "pool"})

    def add_tag_to_dim_if_new(self, dim_id, name):
        for i in range(self.model.rowCount()):
            if self.model.get_tag(i).name == name:
                self.model.get_tag(i).dimension = dim_id
                self.model.dataChanged.emit(self.model.index(i), self.model.index(i))
                return
        self.model.add_tag({"tag_name": name, "dimension": dim_id})

    def set_tag_parent(self, tag, parent_id):
        """设置标签的父级并同步到数据库"""
        # 1. 更新模型
        row = -1
        for i in range(self.model.rowCount()):
            if self.model.get_tag(i).id == tag.id:
                row = i
                break
        
        if row != -1:
            self.model.setData(self.model.index(row), parent_id, TagListModel.PARENT_ID_ROLE)
            
            # 2. 同步数据库
            if tag.id != -1:
                self.service.db.update_tag(tag.id, {"parent_id": parent_id})
            
            self.save_config(silent=True)

    def manage_tag_synonyms(self, tag):
        """弹出对话框管理同义词"""
        from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox
        
        # 获取当前同义词
        all_syns = self.service.db.get_synonyms()
        current_syns = [alias for alias, std in all_syns.items() if std == tag.name]
        
        syn_str = ", ".join(current_syns)
        text, ok = QInputDialog.getText(self, "管理同义词", 
                                       f"标准标签: {tag.name}\n请输入同义词（别名），以逗号分隔:",
                                       QLineEdit.Normal, syn_str)
        
        if ok:
            # 清除旧的映射（仅限当前标准标签的）
            for alias in current_syns:
                self.service.db.remove_synonym(alias)
            
            # 添加新的映射
            new_syns = [s.strip() for s in text.split(",") if s.strip()]
            for s in new_syns:
                try:
                    self.service.db.add_synonym(tag.name, s)
                except Exception as e:
                    QMessageBox.warning(self, "警告", f"别名 '{s}' 已存在或添加失败: {e}")
            
            QMessageBox.information(self, "成功", f"标签 '{tag.name}' 的同义词已更新。")

    def toggle_tag_person_status(self, tag, is_person):
        """将标签标记为人物并同步到数据库"""
        tag.is_person = is_person
        if tag.id != -1:
            self.service.db.update_tag(tag.id, {"is_person": 1 if is_person else 0})
        self.save_config(silent=True)
        QMessageBox.information(self, "人物标记", f"已将标签 '{tag.name}' {'标记' if is_person else '取消标记'}为人物。")

    def save_config(self, silent=False):
        """保存模型中的更改回 tag_config.json 并同步到数据库"""
        # 1. 更新 tag_groups 中的 tags 列表
        tag_map = {} # group_id -> list of tags
        db_tags_to_sync = [] # (dimension, tag_name, color)
        
        for i in range(self.model.rowCount()):
            tag = self.model.get_tag(i)
            if tag.dimension != "pool":
                tag_map.setdefault(tag.dimension, []).append(tag.name)
                db_tags_to_sync.append((tag.dimension, tag.name, tag.color))

        for group in self.tag_config.get("tag_groups", []):
            group["tags"] = tag_map.get(group["id"], [])

        try:
            # 2. 同步到数据库
            # 先通过 service.db 执行更新维度
            for dim, name, color in db_tags_to_sync:
                self.service.db.execute_non_query(
                    "UPDATE tags_library SET dimension = ?, color = ? WHERE tag_name = ?",
                    (dim, color, name)
                )
            
            # 再调用批量添加以确保新标签入库
            bulk_data = [(d, n, 0, None, c) for d, n, c in db_tags_to_sync]
            self.service.db.bulk_add_tags(bulk_data)
            
            # 3. 保存 JSON 配置
            self.service.save_tag_config(self.tag_config)
            
            if not silent:
                QMessageBox.information(self, "成功", "标签库配置已成功同步并保存。")
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "错误", f"保存失败: {e}")
            else:
                print(f"Save config failed: {e}")
