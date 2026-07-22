# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox, QMenu,
    QFileDialog, QProgressDialog, QListView, QScrollArea, QFrame,
    QSplitter, QDialog, QListWidget, QListWidgetItem, QComboBox,
    QToolButton, QSizePolicy, QInputDialog,
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
    """标签使用统计（原热力图看板；默认不占主界面）"""
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
        self.title_label = QLabel("标签使用统计 (Top 10)")
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


class PendingTagsPanel(QFrame):
    """左侧固定待审面板：列表 + 批准到组 / 挂别名 / 丢弃 / AI 建议（须确认）。

    待审词 ≠ 已入库标准词；视觉与右侧标准词组栏区分。
    """

    def __init__(self, owner: "TagsView", colors: dict = None, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.colors = colors or tags_library_palette("dark")
        self.setMinimumWidth(280)
        self.setMaximumWidth(380)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.title_label = QLabel("待审词")
        self.count_label = QLabel("0")
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.hint_label = QLabel(
            "尚未入库的待审词。批准须选目标标签组；"
            "挂别名 / 丢弃 / AI 建议均须确认后写库。"
        )
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(self.list_widget, 1)

        self.group_combo = QComboBox()
        self.group_combo.setToolTip("批准为标准词时的目标标签组（必选）")
        layout.addWidget(self.group_combo)

        btn_row1 = QHBoxLayout()
        self.btn_approve = QPushButton("批准到组")
        self.btn_approve.setObjectName("primary_button")
        self.btn_alias = QPushButton("挂别名")
        btn_row1.addWidget(self.btn_approve)
        btn_row1.addWidget(self.btn_alias)
        layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.btn_discard = QPushButton("丢弃")
        self.btn_ai = QPushButton("AI 建议")
        self.btn_ai.setToolTip("待审词 AI 建议：给出处理方向，人工确认后才写库")
        btn_row2.addWidget(self.btn_discard)
        btn_row2.addWidget(self.btn_ai)
        layout.addLayout(btn_row2)

        self.btn_batch_ai = QPushButton("批量 AI 分拣…")
        self.btn_batch_ai.setToolTip("批量待审 AI 分拣：出清单 → 多选 → 应用选中")
        layout.addWidget(self.btn_batch_ai)

        self.btn_approve.clicked.connect(self._on_approve)
        self.btn_alias.clicked.connect(self._on_alias)
        self.btn_discard.clicked.connect(self._on_discard)
        self.btn_ai.clicked.connect(self._on_ai_suggest)
        self.btn_batch_ai.clicked.connect(self._on_batch_ai)

    def _apply_style(self):
        c = self.colors
        # 待审面板：琥珀色边框，与右侧标准词组栏区分
        accent = "#d48806" if c.get("title") == "#b45309" else "#faad14"
        self.setStyleSheet(f"""
            PendingTagsPanel {{
                background-color: {c["panel_bg"]};
                border-radius: 8px;
                border: 2px solid {accent};
            }}
        """)
        self.title_label.setStyleSheet(
            f"font-weight: bold; color: {accent}; border: none; font-size: 14px;"
        )
        self.count_label.setStyleSheet(
            f"color: {accent}; border: none; font-weight: bold; font-size: 13px;"
        )
        self.hint_label.setStyleSheet(
            f"color: {c['muted']}; border: none; font-size: 11px;"
        )
        self.list_widget.setStyleSheet(
            f"""
            QListWidget {{
                background-color: {c['list_bg']};
                color: {c['input_text']};
                border: 1px solid {c['panel_border']};
                border-radius: 4px;
            }}
            QListWidget::item {{
                padding: 6px 4px;
                border-bottom: 1px solid {c['panel_border']};
            }}
            QListWidget::item:selected {{
                background-color: {c['accent']};
                color: #ffffff;
            }}
            """
        )

    def set_colors(self, colors: dict):
        self.colors = colors
        self._apply_style()

    def _service(self):
        return self.owner.service

    def _tag_config(self):
        return self.owner.tag_config

    def _current_row(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh_group_combo(self):
        current = self.group_combo.currentData()
        self.group_combo.clear()
        for g in self._tag_config().get("tag_groups", []) or []:
            gid = (g.get("id") or "").strip()
            if not gid or gid.lower() == "pool":
                continue
            name = g.get("name") or gid
            self.group_combo.addItem(f"{name} ({gid})", gid)
        if current:
            idx = self.group_combo.findData(current)
            if idx >= 0:
                self.group_combo.setCurrentIndex(idx)

    def reload(self):
        """刷新待审列表与组下拉。"""
        self.refresh_group_combo()
        self.list_widget.clear()
        rows = []
        db = getattr(self._service(), "db", None)
        if db and hasattr(db, "list_pending_tags"):
            try:
                rows = db.list_pending_tags(status="pending") or []
            except Exception as e:
                print(f"list_pending_tags: {e}")
                rows = []
        for row in rows:
            raw = row.get("raw_text") or ""
            gid = row.get("group_id") or ""
            label = f"{raw}"
            if gid:
                label = f"{raw}  · 建议组 {gid}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, row)
            item.setToolTip(f"待审词 #{row.get('id')}：{raw}\n（待审 ≠ 标准词）")
            self.list_widget.addItem(item)
        self.count_label.setText(str(len(rows)))
        if self.list_widget.count() and not self.list_widget.currentItem():
            self.list_widget.setCurrentRow(0)

    def _on_approve(self):
        row = self._current_row()
        if not row:
            QMessageBox.information(self, "提示", "请先选择一条待审词。")
            return
        gid = self.group_combo.currentData()
        if not gid or str(gid).lower() == "pool":
            QMessageBox.warning(self, "目标组无效", "批准为标准词必须选择目标标签组（中转池已废除）。")
            return
        try:
            ok = self._service().resolve_pending_tag(
                row["id"], "approve_standard", group_id=gid
            )
        except Exception as e:
            QMessageBox.critical(self, "批准失败", f"写入标签库时出错：{e}")
            return
        if ok:
            self.reload()
            self.owner.on_pending_resolved()
        else:
            QMessageBox.warning(
                self, "失败",
                "无法批准该待审词（须有效目标组，或同名词冲突未解决）。",
            )

    def _on_alias(self):
        row = self._current_row()
        if not row:
            QMessageBox.information(self, "提示", "请先选择一条待审词。")
            return
        std, ok = QInputDialog.getText(
            self, "挂为别名", f"将「{row.get('raw_text')}」挂到哪个标准词？"
        )
        if not ok or not (std or "").strip():
            return
        if self._service().resolve_pending_tag(
            row["id"], "link_alias", standard_tag=std.strip()
        ):
            self.reload()
            self.owner.on_pending_resolved(refresh_groups=False)
        else:
            QMessageBox.warning(self, "失败", "挂别名失败。")

    def _on_discard(self):
        row = self._current_row()
        if not row:
            return
        reply = QMessageBox.question(
            self,
            "确认丢弃",
            f"确定丢弃待审词「{row.get('raw_text')}」？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if self._service().resolve_pending_tag(row["id"], "discard"):
            self.reload()

    def _on_ai_suggest(self):
        """待审词 AI 建议：后台调模型，完成后弹确认框。"""
        from gui.workers.tag_ai_worker import run_tag_ai_job

        row = self._current_row()
        if not row:
            QMessageBox.information(self, "提示", "请先选择一条待审词。")
            return
        # 拷贝行，避免列表刷新后丢数据
        row_copy = dict(row)
        svc = self._service()

        def work():
            return svc.suggest_pending_tag(row_copy)

        def on_ok(sug):
            if sug is None:
                QMessageBox.warning(self, "失败", "未获得建议。")
                return
            action = getattr(sug, "action", None) or ""
            detail = getattr(sug, "reason", None) or ""
            source = getattr(sug, "source", None) or "rule"
            source_label = "大模型" if source == "model" else "规则降级"
            if action == "link_alias":
                detail += f"\n建议：挂为「{getattr(sug, 'recommended_standard', '')}」的别名"
            elif action == "approve_standard":
                detail += (
                    f"\n建议：批准为标准词"
                    f"（组 {getattr(sug, 'recommended_group_id', None) or getattr(sug, 'group_id', '')}）"
                )
            else:
                detail += "\n建议：丢弃"
            reply = QMessageBox.question(
                self,
                "待审词 AI 建议",
                f"「{row_copy.get('raw_text')}」\n来源：{source_label}\n{detail}\n\n"
                f"是否采纳该建议？\n（未确认不会写库）",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            if self._service().apply_pending_suggestion(sug, confirm=True):
                self.reload()
                self.owner.on_pending_resolved()
            else:
                QMessageBox.warning(self, "失败", "应用建议失败。")

        def on_fail(msg: str):
            QMessageBox.warning(self, "AI 失败", msg or "调用失败")

        run_tag_ai_job(
            self,
            title="待审词 AI",
            label="正在请求 AI 建议，请稍候…\n（网络较慢时可能需要数十秒）",
            fn=work,
            on_ok=on_ok,
            on_fail=on_fail,
            busy_widgets=[self.btn_ai, self.btn_batch_ai] if hasattr(self, "btn_batch_ai") else [self.btn_ai],
        )

    def _on_batch_ai(self):
        """批量待审 AI 分拣：后台生成清单 → 多选 → 应用选中。"""
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QLabel,
        )
        from gui.workers.tag_ai_worker import run_tag_ai_job

        svc = self._service()

        def work():
            return svc.batch_suggest_pending_tags() or []

        def on_ok(suggestions):
            if not suggestions:
                QMessageBox.information(self, "批量分拣", "当前没有待审词。")
                return

            dlg = QDialog(self)
            dlg.setWindowTitle("批量待审 AI 分拣")
            dlg.resize(560, 440)
            layout = QVBoxLayout(dlg)
            layout.addWidget(
                QLabel("默认不预选。勾选后点「应用选中」才写库；未选不写。")
            )
            lst = QListWidget()
            layout.addWidget(lst)
            for sug in suggestions:
                action = getattr(sug, "action", "") or ""
                raw = getattr(sug, "raw_text", "") or ""
                reason = getattr(sug, "reason", "") or ""
                source = getattr(sug, "source", "rule") or "rule"
                src = "模型" if source == "model" else "规则"
                if action == "link_alias":
                    act = f"挂别名→{getattr(sug, 'recommended_standard', '')}"
                elif action == "approve_standard":
                    act = f"批准→{getattr(sug, 'recommended_group_id', None) or getattr(sug, 'group_id', '')}"
                else:
                    act = "丢弃"
                item = QListWidgetItem(f"[{src}] {raw}  ·  {act}  ·  {reason}")
                item.setData(Qt.UserRole, sug)
                item.setCheckState(Qt.Unchecked)
                lst.addItem(item)

            btn_row = QHBoxLayout()
            btn_sel_all = QPushButton("全选")
            btn_clear = QPushButton("清空勾选")
            btn_apply = QPushButton("应用选中")
            btn_apply.setObjectName("primary_button")
            btn_close = QPushButton("关闭")
            btn_row.addWidget(btn_sel_all)
            btn_row.addWidget(btn_clear)
            btn_row.addStretch()
            btn_row.addWidget(btn_apply)
            btn_row.addWidget(btn_close)
            layout.addLayout(btn_row)

            def sel_all():
                for i in range(lst.count()):
                    lst.item(i).setCheckState(Qt.Checked)

            def clear_sel():
                for i in range(lst.count()):
                    lst.item(i).setCheckState(Qt.Unchecked)

            def on_apply():
                chosen = []
                for i in range(lst.count()):
                    it = lst.item(i)
                    if it.checkState() == Qt.Checked:
                        chosen.append(it.data(Qt.UserRole))
                if not chosen:
                    QMessageBox.information(dlg, "提示", "未勾选任何建议。")
                    return
                reply = QMessageBox.question(
                    dlg,
                    "确认应用",
                    f"将应用 {len(chosen)} 条建议到标签库。是否继续？",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                result = self._service().apply_pending_suggestions_selected(
                    chosen, confirm=True
                )
                msg = result.get("message") or f"已应用 {result.get('applied', 0)} 条"
                if result.get("failed"):
                    QMessageBox.warning(
                        dlg,
                        "部分失败",
                        f"{msg}\n请检查日志后重试失败项。",
                    )
                else:
                    QMessageBox.information(dlg, "完成", msg)
                self.reload()
                self.owner.on_pending_resolved()
                dlg.accept()

            btn_sel_all.clicked.connect(sel_all)
            btn_clear.clicked.connect(clear_sel)
            btn_apply.clicked.connect(on_apply)
            btn_close.clicked.connect(dlg.reject)
            dlg.exec()

        def on_fail(msg: str):
            QMessageBox.warning(self, "批量 AI 失败", msg or "调用失败")

        busy = [self.btn_ai]
        if hasattr(self, "btn_batch_ai"):
            busy.append(self.btn_batch_ai)
        run_tag_ai_job(
            self,
            title="批量待审 AI",
            label="正在批量请求 AI 建议，请稍候…\n（待审条数多时会较久，界面不应卡死）",
            fn=work,
            on_ok=on_ok,
            on_fail=on_fail,
            busy_widgets=busy,
        )


class TagsView(QWidget):
    """标签库：左待审面板 + 右标签组栏（无中转池）。"""
    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.import_service = TagImportService(service.ai)
        self.ui_colors = self._resolve_colors()
        
        # 初始化模型
        self.model = TagListModel(self)
        self.column_proxies = {}
        self.pending_panel = None
        self.heatmap_widget = None  # 仅对话框内临时创建
        
        self.setup_ui()
        self.load_data()

    @property
    def tag_config(self):
        """始终使用 service 上的配置，避免 save_tag_config 换对象后界面握着旧空配置。"""
        return self.service.tag_config

    @tag_config.setter
    def tag_config(self, value):
        self.service.tag_config = value
        if hasattr(self.service, "ai") and self.service.ai is not None:
            self.service.ai.tag_config = value

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
        if hasattr(self, "pending_panel") and self.pending_panel is not None:
            self.pending_panel.set_colors(c)
        if hasattr(self, "body_splitter"):
            self.body_splitter.setStyleSheet(
                f"QSplitter::handle {{ background-color: {c['splitter']}; }}"
            )
        if hasattr(self, "splitter"):
            self.splitter.setStyleSheet(
                f"QSplitter::handle {{ background-color: {c['splitter']}; }}"
            )
            # 重建分栏以刷新列表 palette / 输入框 QSS
            self.refresh_columns()

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        self.main_layout.setSpacing(12)
        c = self.ui_colors

        # 顶栏：主操作 + 「更多」次要入口
        header_layout = QHBoxLayout()
        self.title_label = QLabel("标签库")
        self.title_label.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {c['title']};")

        import_btn = QPushButton("导入标签")
        import_btn.clicked.connect(self.import_from_txt)

        save_btn = QPushButton("保存所有更改")
        save_btn.setObjectName("primary_button")
        save_btn.clicked.connect(self.save_config)

        more_btn = QToolButton()
        more_btn.setText("更多 ▾")
        more_btn.setPopupMode(QToolButton.InstantPopup)
        more_btn.setToolTip("次要运营入口：使用统计、近义巡检、词表、Prompt、标签组管理")
        more_menu = QMenu(more_btn)
        more_menu.addAction("标签使用统计…", self.open_usage_stats_dialog)
        more_menu.addAction("近义巡检…", self.open_synonym_audit_dialog)
        more_menu.addAction("加载词表…", self.open_cold_start_dialog)
        more_menu.addSeparator()
        more_menu.addAction("Prompt 配置…", self.open_prompt_config)
        more_menu.addAction("管理标签组…", self.open_group_editor)
        more_btn.setMenu(more_menu)

        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(more_btn)
        header_layout.addWidget(import_btn)
        header_layout.addWidget(save_btn)
        self.main_layout.addLayout(header_layout)

        # 主布局：左待审 + 右标签组栏
        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.setHandleWidth(4)
        self.body_splitter.setStyleSheet(
            f"QSplitter::handle {{ background-color: {c['splitter']}; }}"
        )

        self.pending_panel = PendingTagsPanel(self, self.ui_colors)
        self.body_splitter.addWidget(self.pending_panel)

        # 右侧：仅标签组栏（无中转池、无热力图常驻）
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        main_h_layout = QHBoxLayout(container)
        main_h_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(2)
        self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {c['splitter']}; }}")

        self.refresh_columns()

        main_h_layout.addWidget(self.splitter)
        scroll_area.setWidget(container)
        self.body_splitter.addWidget(scroll_area)

        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setSizes([300, 900])
        self.main_layout.addWidget(self.body_splitter, 1)

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
        
        # 标签组栏（中转池已废除，不再创建 pool 列）
        tag_groups = self.tag_config.get("tag_groups", [])
        for i, group in enumerate(tag_groups):
            color = colors[i % len(colors)]
            column = self.create_column(group["id"], group["name"], color)
            self.splitter.addWidget(column)

    def load_data(self):
        """从配置 + 数据库加载标签；配置中的组标签优先，DB 中已标 dimension 的也归入对应组。"""
        from core.tag_normalize import DEFAULT_CLOSED_GROUP_IDS, DEFAULT_SUGGESTION_GROUP_ID

        # 若配置组内 tags 为空而库中已有对应维度词，回填配置（修复「写入词库后组仍空」）
        if hasattr(self.service, "heal_tag_config_tags_from_db"):
            try:
                self.service.heal_tag_config_tags_from_db()
            except Exception as e:
                print(f"heal_tag_config_tags_from_db: {e}")

        known_groups = {
            g.get("id")
            for g in (self.tag_config.get("tag_groups") or [])
            if g.get("id")
        }
        known_groups |= set(DEFAULT_CLOSED_GROUP_IDS) | {DEFAULT_SUGGESTION_GROUP_ID}

        # 1. 配置中明确列出的标准词
        config_tags = {}
        for group in self.tag_config.get("tag_groups", []):
            group_id = group.get("id")
            if not group_id:
                continue
            for tag_name in group.get("tags", []) or []:
                name = tag_name if isinstance(tag_name, str) else (
                    tag_name.get("name") if isinstance(tag_name, dict) else None
                )
                if name:
                    config_tags[str(name)] = group_id

        # 2. 从数据库加载详情
        tags_detail = self.service.db.get_tags_detail() or []
        detail_map = {t["tag_name"]: t for t in tags_detail if t.get("tag_name")}
        formatted_data = []
        processed_names = set()

        for tag_name, group_id in config_tags.items():
            detail = detail_map.get(tag_name)
            if detail:
                formatted_data.append({
                    "id": detail["id"],
                    "tag_name": tag_name,
                    "dimension": group_id,
                    "usage_count": detail.get("usage_count") or 0,
                    "color": detail.get("color"),
                    "parent_id": detail.get("parent_id"),
                })
            else:
                formatted_data.append({
                    "id": -1,
                    "tag_name": tag_name,
                    "dimension": group_id,
                    "usage_count": 0,
                    "color": None,
                    "parent_id": None,
                })
            processed_names.add(tag_name)

        # 3. DB 中其余标签：仅归入已知标签组；无组词应由迁移进待审，此处跳过（不进 pool）
        for t in tags_detail:
            name = t.get("tag_name")
            if not name or name in processed_names:
                continue
            dim = (t.get("dimension") or "").strip()
            dim_key = dim.lower() if dim.lower() in known_groups else dim
            if dim_key in known_groups and dim_key != "pool":
                formatted_data.append({
                    "id": t["id"],
                    "tag_name": name,
                    "dimension": dim_key,
                    "usage_count": t.get("usage_count") or 0,
                    "color": t.get("color"),
                    "parent_id": t.get("parent_id"),
                })
            # 无合法组：不展示为标准词（服务层 migrate_pool 会降为待审）
            processed_names.add(name)

        self.model.set_tags(formatted_data)

        if hasattr(self, "pending_panel") and self.pending_panel is not None:
            self.pending_panel.reload()

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
        
        # 使用统计仅在对话框打开时刷新（主界面不常驻）
        heat = getattr(self, "_usage_stats_widget", None)
        if heat is not None:
            try:
                heat.update_data()
            except RuntimeError:
                self._usage_stats_widget = None

    def on_pending_resolved(self, refresh_groups: bool = True):
        """待审处理成功后：刷新右侧组栏（批准进组）并同步模型。"""
        if refresh_groups:
            self.load_data()
            # 组栏已随 model 过滤更新；保存配置使标准词落盘
            self.save_config(silent=True)

    def open_usage_stats_dialog(self):
        """标签使用统计：次要入口，不占主界面。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("标签使用统计")
        dlg.resize(720, 220)
        layout = QVBoxLayout(dlg)
        heat = TagHeatmapWidget(self.model, self.ui_colors, dlg)
        heat.setMinimumHeight(140)
        heat.setMaximumHeight(16777215)
        heat.setFixedHeight(160)
        self._usage_stats_widget = heat
        layout.addWidget(heat)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)

        def _clear():
            self._usage_stats_widget = None

        dlg.finished.connect(_clear)
        dlg.exec()

    def open_pending_tags_dialog(self):
        """兼容旧入口：聚焦左侧待审面板并刷新。"""
        if self.pending_panel is not None:
            self.pending_panel.reload()
            self.pending_panel.setFocus()
            if self.body_splitter is not None:
                sizes = self.body_splitter.sizes()
                if sizes and sizes[0] < 200:
                    self.body_splitter.setSizes([300, max(sizes[1], 600)])

    def open_standard_tag_ai_assist(self, tag):
        """标准词 AI 助手：后台取建议 → 对话框确认后写库。"""
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QCheckBox,
            QLineEdit,
            QPushButton,
            QMessageBox,
            QComboBox,
        )
        from gui.workers.tag_ai_worker import run_tag_ai_job

        name = tag.name if hasattr(tag, "name") else str(tag)
        dim = tag.dimension if hasattr(tag, "dimension") else ""
        svc = self.service

        def work():
            return svc.suggest_standard_tag_assist(name, dim)

        def on_ok(sug):
            if sug is None:
                QMessageBox.warning(self, "失败", "未获得建议。")
                return
            source = getattr(sug, "source", "rule") or "rule"
            src_label = "大模型" if source == "model" else "规则降级"

            dlg = QDialog(self)
            dlg.setWindowTitle(f"标准词 AI 助手 — {name}")
            dlg.resize(480, 360)
            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel(f"来源：{src_label}\n{getattr(sug, 'reason', '') or ''}"))

            aliases_edit = QLineEdit("、".join(getattr(sug, "suggested_aliases", None) or []))
            aliases_edit.setPlaceholderText("建议别名，顿号分隔")
            layout.addWidget(QLabel("建议别名："))
            layout.addWidget(aliases_edit)
            cb_alias = QCheckBox("采纳别名")
            cb_alias.setChecked(bool(getattr(sug, "suggested_aliases", None)))
            layout.addWidget(cb_alias)

            layout.addWidget(QLabel("建议改组："))
            group_combo = QComboBox()
            group_combo.addItem("（不改组）", "")
            for g in (self.tag_config or {}).get("tag_groups", []) or []:
                gid = (g.get("id") or "").strip()
                if not gid or gid.lower() == "pool":
                    continue
                group_combo.addItem(f"{g.get('name') or gid} ({gid})", gid)
            rg = getattr(sug, "recommended_group_id", None) or ""
            if rg:
                idx = group_combo.findData(rg)
                if idx >= 0:
                    group_combo.setCurrentIndex(idx)
            cb_move = QCheckBox("采纳改组")
            cb_move.setChecked(bool(rg))
            layout.addWidget(group_combo)
            layout.addWidget(cb_move)

            btn_row = QHBoxLayout()
            btn_ok = QPushButton("确认应用")
            btn_cancel = QPushButton("取消")
            btn_row.addStretch()
            btn_row.addWidget(btn_ok)
            btn_row.addWidget(btn_cancel)
            layout.addLayout(btn_row)

            def on_confirm():
                if not cb_alias.isChecked() and not cb_move.isChecked():
                    QMessageBox.information(dlg, "提示", "请至少勾选一项。")
                    return
                aliases = [
                    a.strip()
                    for a in aliases_edit.text().replace(",", "、").split("、")
                    if a.strip()
                ]
                from core.tag_ai_assist import StandardTagAssistSuggestion

                final = StandardTagAssistSuggestion(
                    tag_name=name,
                    current_group_id=str(dim or ""),
                    suggested_aliases=aliases,
                    recommended_group_id=group_combo.currentData() or None,
                    reason=getattr(sug, "reason", "") or "",
                    source=source,
                )
                result = self.service.apply_standard_tag_assist(
                    final,
                    apply_aliases=cb_alias.isChecked(),
                    apply_regroup=cb_move.isChecked(),
                    confirm=True,
                )
                if result.get("ok"):
                    skip = result.get("aliases_skipped") or 0
                    extra = f"；跳过标准词别名 {skip}" if skip else ""
                    QMessageBox.information(
                        dlg,
                        "完成",
                        f"别名 +{result.get('aliases_added', 0)}；改组={'是' if result.get('moved') else '否'}{extra}",
                    )
                    self.load_data()
                    dlg.accept()
                else:
                    QMessageBox.warning(dlg, "失败", result.get("message") or "应用失败")

            btn_ok.clicked.connect(on_confirm)
            btn_cancel.clicked.connect(dlg.reject)
            dlg.exec()

        def on_fail(msg: str):
            QMessageBox.warning(self, "标准词 AI 失败", msg or "调用失败")

        run_tag_ai_job(
            self,
            title="标准词 AI 助手",
            label=f"正在为「{name}」请求 AI 建议…",
            fn=work,
            on_ok=on_ok,
            on_fail=on_fail,
        )

    def open_synonym_audit_dialog(self):
        """近义巡检：后台生成建议 → 人工确认后写库。"""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
            QPushButton, QLabel, QMessageBox,
        )
        from gui.workers.tag_ai_worker import run_tag_ai_job

        svc = self.service

        def work():
            return svc.audit_synonyms() or []

        def on_ok(suggestions):
            dlg = QDialog(self)
            dlg.setWindowTitle("近义巡检")
            dlg.resize(560, 420)
            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel("以下为可能重复的标准词建议。未确认前不会写库。"))
            lst = QListWidget()
            layout.addWidget(lst)
            for s in suggestions:
                aliases = "、".join(s.merge_as_aliases or [])
                src = "模型" if getattr(s, "source", "") == "model" else "规则"
                item = QListWidgetItem(
                    f"[{src}] 保留「{s.keep}」← 合并 {aliases}  ({s.reason})"
                )
                item.setData(Qt.UserRole, s)
                item.setCheckState(Qt.Unchecked)
                lst.addItem(item)
            if not suggestions:
                layout.addWidget(QLabel("未发现可合并的近义对。"))
            layout.addWidget(QLabel("默认不预选。勾选后点「确认合并勾选项」才写库。"))
            btn_row = QHBoxLayout()
            btn_apply = QPushButton("确认合并勾选项")
            btn_close = QPushButton("关闭")
            btn_row.addWidget(btn_apply)
            btn_row.addStretch()
            btn_row.addWidget(btn_close)
            layout.addLayout(btn_row)

            def on_apply():
                chosen = []
                for i in range(lst.count()):
                    it = lst.item(i)
                    if it.checkState() == Qt.Checked:
                        chosen.append(it.data(Qt.UserRole))
                if not chosen:
                    QMessageBox.information(dlg, "提示", "未勾选任何建议")
                    return
                reply = QMessageBox.question(
                    dlg, "确认合并",
                    f"将执行 {len(chosen)} 组合并（别名挂接 + 视频标签归一）。是否继续？",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                result = self.service.apply_synonym_merge_suggestions(chosen, confirm=True)
                msg = result.get("message") or f"已应用 {result.get('applied', 0)} 组合并"
                if result.get("failed"):
                    QMessageBox.warning(dlg, "部分失败", msg)
                else:
                    QMessageBox.information(dlg, "完成", msg)
                self.load_data()
                dlg.accept()

            btn_apply.clicked.connect(on_apply)
            btn_close.clicked.connect(dlg.reject)
            dlg.exec()

        def on_fail(msg: str):
            QMessageBox.warning(self, "近义巡检失败", msg or "调用失败")

        run_tag_ai_job(
            self,
            title="近义巡检",
            label="正在请求 AI 近义巡检，请稍候…",
            fn=work,
            on_ok=on_ok,
            on_fail=on_fail,
        )

    def open_cold_start_dialog(self):
        """词表加载：直接读取 词.txt 语义（分组 + 标准词 ← 别名），无分簇加工。"""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
            QLabel, QMessageBox, QFileDialog, QCheckBox,
        )
        from core.tag_vocab import commit_plan_summary
        from core.video_organizer_service import DICTIONARY_FILE
        import os

        dlg = QDialog(self)
        dlg.setWindowTitle("加载词表")
        dlg.resize(720, 560)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            "直接加载词表文件（默认项目内 词.txt）。\n"
            "格式：分组标题 # === … mood/subject/… ===；行内「标准词」或「标准词 ← 别名1, 别名2」。\n"
            "不做 AI/规则分簇，不做精瘦裁剪；预览后「写入标签库」才会保存。"
        ))
        draft_edit = QTextEdit()
        draft_edit.setPlaceholderText(
            "每行一个词，或：\n男性 ← 男, 男人\n# ========== 氛围 mood =========="
        )
        layout.addWidget(draft_edit)
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setPlaceholderText("点击「解析预览」查看分组标准词与别名…")
        layout.addWidget(QLabel("解析预览"))
        layout.addWidget(preview)

        chk_replace_ph = QCheckBox("写入时清除占位测试词（如「测试氛围1」）")
        chk_replace_ph.setChecked(True)
        chk_replace_all = QCheckBox("整组替换为词表内容（推荐：以词.txt 为准覆盖各组标准词）")
        chk_replace_all.setChecked(True)
        layout.addWidget(chk_replace_ph)
        layout.addWidget(chk_replace_all)

        state = {"draft": None}

        def load_path(path: str):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    draft_edit.setPlainText(f.read())
            except Exception as e:
                QMessageBox.critical(dlg, "错误", str(e))

        def load_default_vocab():
            path = DICTIONARY_FILE
            if not os.path.isfile(path):
                QMessageBox.warning(dlg, "提示", f"未找到默认词表：\n{path}")
                return
            load_path(path)

        def load_file():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "选择词表文件", os.path.dirname(DICTIONARY_FILE) or "", "Text (*.txt);;All (*.*)"
            )
            if path:
                load_path(path)

        def do_preview():
            lines = draft_edit.toPlainText().splitlines()
            draft = self.service.build_cold_start_draft(lines, direct=True, use_ai=False)
            state["draft"] = draft
            summary = commit_plan_summary(draft)
            lines_out = ["## 各组标准词", ""]
            for gid, tags in summary["groups"].items():
                lines_out.append(f"- {gid} ({len(tags)}): {', '.join(tags) if tags else '（空）'}")
            lines_out.append("")
            lines_out.append(f"## 别名数量: {summary['alias_count']}")
            for a, s in list(summary["aliases"].items())[:80]:
                lines_out.append(f"  {a} → {s}")
            notes = list(summary.get("notes") or []) + list(getattr(draft, "notes", None) or [])
            if notes:
                lines_out.append("")
                lines_out.append("## 说明")
                seen_n = set()
                for n in notes:
                    if n in seen_n:
                        continue
                    seen_n.add(n)
                    lines_out.append(f"- {n}")
            preview.setPlainText("\n".join(lines_out))

        def do_commit():
            if state["draft"] is None:
                do_preview()
            if state["draft"] is None:
                QMessageBox.warning(dlg, "提示", "请先解析预览")
                return
            reply = QMessageBox.question(
                dlg, "确认写入",
                "确定将词表写入标签库与别名表？此操作会修改配置。",
            )
            if reply != QMessageBox.Yes:
                return
            result = self.service.commit_cold_start_draft(
                state["draft"],
                replace_placeholders=chk_replace_ph.isChecked(),
                replace_all_group_tags=chk_replace_all.isChecked(),
                cap_closed_groups=False,
            )
            if result.get("ok"):
                counts = result.get("group_counts") or {}
                detail = "、".join(f"{k}:{v}" for k, v in counts.items()) or "已写入"
                QMessageBox.information(
                    dlg, "完成", f"词表已写入标签库。\n各组词数：{detail}"
                )
                # property setter 同步到 service
                self.tag_config = self.service.tag_config
                self.refresh_columns()
                self.load_data()
                dlg.accept()
            else:
                QMessageBox.warning(dlg, "失败", str(result.get("error") or "写入失败"))

        if os.path.isfile(DICTIONARY_FILE):
            load_path(DICTIONARY_FILE)

        btn_row = QHBoxLayout()
        btn_default = QPushButton("加载默认词.txt")
        btn_file = QPushButton("从其他文件…")
        btn_preview = QPushButton("解析预览")
        btn_commit = QPushButton("写入标签库")
        btn_commit.setObjectName("primary_button")
        btn_cancel = QPushButton("取消（不写库）")
        btn_default.clicked.connect(load_default_vocab)
        btn_file.clicked.connect(load_file)
        btn_preview.clicked.connect(do_preview)
        btn_commit.clicked.connect(do_commit)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_default)
        btn_row.addWidget(btn_file)
        btn_row.addWidget(btn_preview)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_commit)
        layout.addLayout(btn_row)
        dlg.exec()

    def open_group_editor(self):
        from gui.views.tags_library_editors import TagGroupEditor
        from core.tag_vocab import ensure_canonical_tag_groups

        # 保证五组骨架存在，并尽量从 DB 回填空组
        if hasattr(self.service, "heal_tag_config_tags_from_db"):
            try:
                self.service.heal_tag_config_tags_from_db()
            except Exception:
                pass
        try:
            ensured = ensure_canonical_tag_groups(self.service.tag_config or {})
            self.service.tag_config = ensured
            if hasattr(self.service, "ai") and self.service.ai:
                self.service.ai.tag_config = ensured
        except Exception:
            pass

        dialog = TagGroupEditor(
            self.service.tag_config, self, service=self.service
        )
        dialog.exec()
        # 组编辑可能已通过 service.delete_tag_group 落盘；再同步一次配置引用
        self.tag_config = self.service.tag_config or self.tag_config
        try:
            self.service.save_tag_config(self.service.tag_config)
        except Exception:
            pass
        self.refresh_columns()
        self.load_data()
        if self.pending_panel is not None:
            self.pending_panel.reload()

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
        if not group:
            return

        text, ok = QInputDialog.getMultiLineText(
            self,
            "编辑局部 Prompt",
            f"组 '{group['name']}' 的 AI 引导词:",
            group.get("rules", {}).get("local_prompt", ""),
        )
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
                self.save_config(silent=True)  # 实时响应数据库
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

        move_targets = []
        for group in self.tag_config.get("tag_groups", []):
            gid = group.get("id")
            if not gid or str(gid).lower() == "pool":
                continue
            move_targets.append((gid, group.get("name") or gid))

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

            for c in sorted(candidates, key=lambda x: x.name)[:20]:  # 限制数量防止菜单太长
                act = parent_menu.addAction(c.name)
                act.triggered.connect(lambda checked=False, tag=current_tag, p_id=c.id: self.set_tag_parent(tag, p_id))

            # 同义词管理
            synonym_act = menu.addAction("管理别名…")
            synonym_act.triggered.connect(lambda: self.manage_tag_synonyms(current_tag))

            ai_assist_act = menu.addAction("标准词 AI 助手…")
            ai_assist_act.triggered.connect(
                lambda: self.open_standard_tag_ai_assist(current_tag)
            )

            # 人脸识别联动：标记为人物 (V6.0)
            person_act = menu.addAction("👤 标记为'人物' (用于人脸联动)")
            person_act.setCheckable(True)
            person_act.setChecked(current_tag.is_person)
            person_act.triggered.connect(lambda checked: self.toggle_tag_person_status(current_tag, checked))

            menu.addSeparator()

        # 批量删除（QAction 无 setStyleSheet，勿调用）
        delete_act = menu.addAction("批量删除")
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
        new_name, ok = QInputDialog.getText(
            self, "合并标签", "请输入合并后的标签名称:",
            QLineEdit.Normal, first_tag_name,
        )

        if ok and new_name.strip():
            source_rows = [idx.model().mapToSource(idx).row() for idx in selected_indexes]
            old_names = []
            keep_dim = "custom"
            for r in sorted(source_rows):
                t = self.model.get_tag(r)
                if t and t.name:
                    old_names.append(t.name)
                    if t.dimension and str(t.dimension).lower() != "pool":
                        keep_dim = t.dimension
            target = new_name.strip()
            self.model.merge_tags(source_rows, target)
            for n in old_names:
                if n != target and hasattr(self.service, "bulk_replace_tags"):
                    try:
                        self.service.bulk_replace_tags(n, target)
                    except Exception:
                        pass
                if n != target:
                    try:
                        self.service.db.delete_tag_by_name(n)
                    except Exception:
                        pass
            try:
                self.service.db.add_tag(keep_dim, target, is_learned=0)
                self.service.db.reassign_tags_dimension([target], keep_dim)
            except Exception:
                pass
            # 合并后模型中目标词维度对齐
            for i in range(self.model.rowCount()):
                t = self.model.get_tag(i)
                if t and t.name == target:
                    t.dimension = keep_dim
                    break
            self.save_config(silent=True)

    def batch_delete_tags(self, list_view: QListView):
        selected_indexes = list_view.selectedIndexes()
        if not selected_indexes:
            return

        if QMessageBox.question(
            self, "确认删除", f"确定要删除选中的 {len(selected_indexes)} 个标签吗？",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.No:
            return

        # 必须从后往前删；同步删除 DB 标准词
        source_rows = sorted(
            [idx.model().mapToSource(idx).row() for idx in selected_indexes],
            reverse=True,
        )
        for row in source_rows:
            tag = self.model.get_tag(row)
            if tag and tag.name:
                try:
                    self.service.db.delete_tag_by_name(
                        tag.name,
                        tag.dimension if tag.dimension and tag.dimension.lower() != "pool" else None,
                    )
                except Exception:
                    pass
            self.model.remove_tag(row)
        self.save_config(silent=True)

    def import_from_txt(self):
        from PySide6.QtWidgets import QWizard
        wizard = TagImportWizard(self.import_service, self)
        if wizard.exec() == QWizard.Accepted:
            results = getattr(wizard, "final_classified", {}) or {}
            # 服务层落盘（拒绝 pool / 未知组）
            ok, err = True, ""
            if hasattr(self.import_service, "persist_classified_tags"):
                ok, err = self.import_service.persist_classified_tags(results)
            if not ok:
                QMessageBox.warning(self, "导入失败", err or "无法写入标签库")
                return
            # DB + 内存模型同步
            for cat_id, tags in results.items():
                dim = (cat_id or "").strip()
                if not dim or dim.lower() == "pool":
                    for tag_name in tags:
                        self.add_ungrouped_as_pending(tag_name)
                    continue
                for tag_name in tags:
                    name = (tag_name or "").strip()
                    if not name:
                        continue
                    try:
                        self.service.db.add_tag(dim, name, is_learned=0)
                    except Exception:
                        pass
                    self.add_tag_to_dim_if_new(dim, name)
            # 与文件配置对齐
            try:
                self.service.tag_config = self.import_service.ai.tag_config or self.service.tag_config
                self.tag_config = self.service.tag_config
            except Exception:
                pass
            self.save_config(silent=True)
            self.load_data()
            if self.pending_panel is not None:
                self.pending_panel.reload()
            QMessageBox.information(self, "导入完成", "标签已成功导入并写入标签库。")

    def add_ungrouped_as_pending(self, name):
        """无合法组时的新词：进入待审，不写 pool 标准词。"""
        for i in range(self.model.rowCount()):
            if self.model.get_tag(i).name == name:
                return
        if hasattr(self.service, "db") and hasattr(self.service.db, "add_pending_tag"):
            self.service.db.add_pending_tag(name, group_id="", source_path=None)
            if self.pending_panel is not None:
                self.pending_panel.reload()
        else:
            self.model.add_tag({"tag_name": name, "dimension": "custom"})

    def add_tag_to_pool_if_new(self, name):
        """兼容旧名：同 add_ungrouped_as_pending。"""
        self.add_ungrouped_as_pending(name)

    def add_tag_to_dim_if_new(self, dim_id, name):
        dim = (dim_id or "").strip()
        if not dim or dim.lower() == "pool":
            self.add_ungrouped_as_pending(name)
            return
        for i in range(self.model.rowCount()):
            if self.model.get_tag(i).name == name:
                self.model.get_tag(i).dimension = dim
                self.model.dataChanged.emit(self.model.index(i), self.model.index(i))
                return
        self.model.add_tag({"tag_name": name, "dimension": dim})

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
        """弹出对话框管理别名（标准词的其它写法）。"""
        # 获取当前同义词
        all_syns = self.service.db.get_synonyms()
        current_syns = [alias for alias, std in all_syns.items() if std == tag.name]

        syn_str = ", ".join(current_syns)
        text, ok = QInputDialog.getText(
            self, "管理别名",
            f"标准词: {tag.name}\n请输入别名，以逗号分隔:",
            QLineEdit.Normal, syn_str,
        )

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

            QMessageBox.information(self, "成功", f"标准词 '{tag.name}' 的别名已更新。")

    def toggle_tag_person_status(self, tag, is_person):
        """将标签标记为人物并同步到数据库"""
        tag.is_person = is_person
        if tag.id != -1:
            self.service.db.update_tag(tag.id, {"is_person": 1 if is_person else 0})
        self.save_config(silent=True)
        QMessageBox.information(
            self, "人物标记",
            f"已将标签 '{tag.name}' {'标记' if is_person else '取消标记'}为人物。",
        )

    def save_config(self, silent=False):
        """保存模型中的更改回 tag_config.json 并同步到数据库。

        注意：禁止在「模型完全为空」时用空列表覆盖配置里已有标准词
        （否则加载词表写入后若 UI 尚未填满 model，一次 silent save 会把组 tags 清空）。
        """
        tag_map = {}  # group_id -> list of tags
        db_tags_to_sync = []  # (dimension, tag_name, color)

        for i in range(self.model.rowCount()):
            tag = self.model.get_tag(i)
            if tag.dimension != "pool":
                tag_map.setdefault(tag.dimension, []).append(tag.name)
                db_tags_to_sync.append((tag.dimension, tag.name, tag.color))

        model_has_grouped = any(tag_map.values())

        for group in self.tag_config.get("tag_groups", []):
            gid = group.get("id")
            if not gid:
                continue
            if gid in tag_map:
                group["tags"] = list(dict.fromkeys(tag_map[gid]))
            elif model_has_grouped:
                # 模型有其它组的数据，本组确实为空
                group["tags"] = []
            # else: 模型整体无分组标签 → 保留 group 原有 tags，避免误清空

        try:
            for dim, name, color in db_tags_to_sync:
                # 安全改派：按 id 保留目标组一行，避免 UNIQUE(dimension, tag_name)
                self.service.db.reassign_tags_dimension([name], dim)
                self.service.db.execute_non_query(
                    "UPDATE tags_library SET color = ? WHERE tag_name = ? AND LOWER(dimension) = LOWER(?)",
                    (color, name, dim),
                )

            bulk_data = [(d, n, 0, None, c) for d, n, c in db_tags_to_sync]
            if bulk_data:
                self.service.db.bulk_add_tags(bulk_data)

            self.service.save_tag_config(self.tag_config)

            if not silent:
                QMessageBox.information(self, "成功", "标签库配置已成功同步并保存。")
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "错误", f"保存失败: {e}")
            else:
                print(f"Save config failed: {e}")
