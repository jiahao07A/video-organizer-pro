# -*- coding: utf-8 -*-
import os
import sys
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, 
    QComboBox, QFormLayout, QScrollArea, QFrame, QCheckBox, 
    QPushButton, QMessageBox, QMenu, QToolButton
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from .tag_flow import TagFlowWidget
from .media_preview import MediaPreviewWidget
from core.video_organizer_service import VideoOrganizerService

class DetailPanel(QWidget):
    """详情编辑面板"""
    data_changed = Signal() # 数据保存后发出的信号

    def __init__(self, service: VideoOrganizerService, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = service.settings
        self.current_video = None # 可以是单个 dict 或 list
        self.setup_ui()
        self.apply_field_preferences()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # 批量操作提示
        self.batch_info = QLabel("")
        self.batch_info.setStyleSheet("color: #3d5afe; font-weight: bold;")
        self.batch_info.setVisible(False)
        layout.addWidget(self.batch_info)

        # 1. 素材预览区（放大）
        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("素材预览"))
        preview_header.addStretch()
        self.field_pref_btn = QToolButton()
        self.field_pref_btn.setText("显示字段")
        self.field_pref_btn.setPopupMode(QToolButton.InstantPopup)
        self.field_pref_btn.setToolTip("选择详情区显示/隐藏的字段")
        preview_header.addWidget(self.field_pref_btn)
        layout.addLayout(preview_header)

        self.media_preview = MediaPreviewWidget()
        self.media_preview.setMinimumHeight(260)
        layout.addWidget(self.media_preview, 2)

        # 兼容旧入口
        self.thumb_label = self.media_preview

        # 2. 表单区域
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.NoFrame)
        
        form_container = QWidget()
        form_layout = QFormLayout(form_container)
        form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form_layout.setLabelAlignment(Qt.AlignRight)
        
        self.filename_label = QLabel("-")
        self.filename_label.setWordWrap(True)
        self.filename_label.setStyleSheet("font-weight: bold; color: #3d5afe;")
        
        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.addItems(self.settings.get("categories", []))
        
        self.tags_widget = TagFlowWidget()
        self.refresh_tag_completer()
        
        self.summary_input = QTextEdit()
        self.summary_input.setMaximumHeight(100)
        
        self.transcript_input = QTextEdit()
        self.transcript_input.setPlaceholderText("音频转录文本...")
        
        self._field_rows = {}
        self._field_rows["filename"] = form_layout.rowCount()
        form_layout.addRow("文件名:", self.filename_label)
        self._field_rows["category"] = form_layout.rowCount()
        form_layout.addRow("分类:", self.category_input)
        self._field_rows["tags"] = form_layout.rowCount()
        form_layout.addRow("标签:", self.tags_widget)
        self._field_rows["summary"] = form_layout.rowCount()
        form_layout.addRow("摘要:", self.summary_input)
        self._field_rows["transcription"] = form_layout.rowCount()
        form_layout.addRow("转录:", self.transcript_input)
        self._form_layout = form_layout
        
        form_scroll.setWidget(form_container)
        layout.addWidget(form_scroll, 3)

        # 批量设置
        self.merge_tags_cb = QCheckBox("合并标签 (不覆盖原有标签)")
        self.merge_tags_cb.setChecked(True)
        layout.addWidget(self.merge_tags_cb)

        # 3. 保存按钮
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存修改")
        self.save_btn.setObjectName("primary_button")
        self.save_btn.setFixedHeight(40)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_changes)
        
        self.export_tags_btn = QPushButton("导出标签云")
        self.export_tags_btn.setFixedHeight(40)
        self.export_tags_btn.clicked.connect(self.export_tag_cloud)

        self.ai_rec_btn = QPushButton("AI 推荐标签")
        self.ai_rec_btn.setFixedHeight(40)
        self.ai_rec_btn.clicked.connect(self.run_ai_recommendation)
        self.ai_rec_btn.setEnabled(False)

        self.storyboard_btn = QPushButton("生成故事板")
        self.storyboard_btn.setFixedHeight(40)
        self.storyboard_btn.clicked.connect(self.generate_storyboard)
        self.storyboard_btn.setEnabled(False)
        
        btn_layout.addWidget(self.save_btn, 2)
        btn_layout.addWidget(self.ai_rec_btn, 1)
        btn_layout.addWidget(self.storyboard_btn, 1)
        btn_layout.addWidget(self.export_tags_btn, 1)
        layout.addLayout(btn_layout)

        self._build_field_pref_menu()

    def _build_field_pref_menu(self):
        menu = QMenu(self)
        prefs = (self.settings.get("ui_preferences") or {}).get("detail_fields_visible") or {}
        defaults = {
            "filename": True,
            "category": True,
            "tags": True,
            "summary": True,
            "transcription": True,
        }
        self._field_actions = {}
        labels = {
            "filename": "文件名",
            "category": "分类",
            "tags": "标签",
            "summary": "摘要",
            "transcription": "转录",
        }
        for key, lab in labels.items():
            act = menu.addAction(lab)
            act.setCheckable(True)
            act.setChecked(prefs.get(key, defaults[key]))
            act.toggled.connect(lambda checked, k=key: self._on_field_pref(k, checked))
            self._field_actions[key] = act
        self.field_pref_btn.setMenu(menu)

    def _on_field_pref(self, key: str, visible: bool):
        prefs = self.settings.setdefault("ui_preferences", {})
        fields = prefs.setdefault("detail_fields_visible", {})
        fields[key] = visible
        try:
            from core.video_organizer_service import SettingsManager
            SettingsManager.save_settings(self.settings, self.service.db)
        except Exception:
            pass
        self.apply_field_preferences()

    def apply_field_preferences(self):
        prefs = (self.settings.get("ui_preferences") or {}).get("detail_fields_visible") or {}
        mapping = {
            "filename": self.filename_label,
            "category": self.category_input,
            "tags": self.tags_widget,
            "summary": self.summary_input,
            "transcription": self.transcript_input,
        }
        for key, widget in mapping.items():
            show = prefs.get(key, True)
            widget.setVisible(show)
            # 隐藏对应标签
            label = self._form_layout.labelForField(widget)
            if label:
                label.setVisible(show)

    def export_tag_cloud(self):
        """导出标签统计报告 (标签云入口)"""
        videos = self.service.get_all_videos()
        if not videos:
            QMessageBox.warning(self, "警告", "没有视频数据可导出。")
            return
            
        # 统计标签
        tag_counts = {}
        for v in videos:
            for t in v.get("tags", []):
                tag_counts[t] = tag_counts.get(t, 0) + 1
        
        if not tag_counts:
            QMessageBox.warning(self, "警告", "没有发现任何标签。")
            return
            
        # 排序
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        
        # 简单生成文本报告
        report = "标签使用频率统计 (Top 50):\n\n"
        for tag, count in sorted_tags[:50]:
            report += f"{tag}: {count}\n"
            
        # 也可以尝试保存为文件
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getSaveFileName(self, "保存标签统计", "", "Text Files (*.txt);;CSV Files (*.csv)")
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    if file_path.endswith(".csv"):
                        f.write("Tag,Count\n")
                        for tag, count in sorted_tags:
                            f.write(f"{tag},{count}\n")
                    else:
                        f.write(report)
                QMessageBox.information(self, "成功", f"统计报告已保存至: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存失败: {e}")

    def run_ai_recommendation(self):
        """运行 AI 标签推荐"""
        if not self.current_video or isinstance(self.current_video, list):
            return
            
        current_tags = self.tags_widget.tags
        self.ai_rec_btn.setText("推荐中...")
        self.ai_rec_btn.setEnabled(False)
        
        # 使用线程避免 UI 卡死
        from PySide6.QtCore import QThread, Signal
        class RecWorker(QThread):
            finished = Signal(list)
            def __init__(self, ai, tags):
                super().__init__()
                self.ai = ai
                self.tags = tags
            def run(self):
                res = self.ai.recommend_tags(self.tags)
                self.finished.emit(res)
        
        self.worker = RecWorker(self.service.ai, current_tags)
        def on_finished(recs):
            self.ai_rec_btn.setText("AI 推荐标签")
            self.ai_rec_btn.setEnabled(True)
            if not recs:
                QMessageBox.information(self, "结果", "未找到相关的标签推荐。")
                return
                
            # 弹出选择对话框
            rec_texts = [f"{r['tag']} ({r['reason']})" for r in recs]
            from PySide6.QtWidgets import QInputDialog
            item, ok = QInputDialog.getItem(self, "AI 标签推荐", "选择要添加的标签:", rec_texts, 0, False)
            if ok and item:
                tag_name = item.split(" (")[0]
                if tag_name not in self.tags_widget.tags:
                    self.tags_widget.add_tag_chip(tag_name)
                    self.tags_widget.tags_changed.emit(self.tags_widget.tags)

        self.worker.finished.connect(on_finished)
        self.worker.start()

    def generate_storyboard(self):
        """为当前视频生成故事板"""
        if not self.current_video or isinstance(self.current_video, list):
            return
            
        path = self.current_video.get("path")
        if not path: return
        
        from PySide6.QtWidgets import QFileDialog
        out_path, _ = QFileDialog.getSaveFileName(self, "保存故事板", os.path.splitext(path)[0] + "_storyboard.pdf", "PDF Files (*.pdf);;Image Files (*.jpg *.png)")
        
        if out_path:
            success = self.service.generate_storyboard(path, out_path)
            if success:
                QMessageBox.information(self, "成功", f"故事板已生成并保存至:\n{out_path}")
            else:
                QMessageBox.critical(self, "失败", "故事板生成失败，请检查日志。")

    def on_thumb_double_click(self, event):
        """兼容右键播放：切换素材预览播放。"""
        if hasattr(self, "media_preview") and self.media_preview:
            self.media_preview.toggle_play()

    def load_video_data(self, video_data):
        """填充数据到面板，支持单选或多选"""
        if isinstance(video_data, list):
            self.current_video = video_data
            count = len(video_data)
            self.batch_info.setText(f"正在批量编辑 {count} 个视频")
            self.batch_info.setVisible(True)
            self.save_btn.setEnabled(True)
            
            self.filename_label.setText("多个视频...")
            self.category_input.setCurrentText("")
            self.tags_widget.set_tags([])
            self.summary_input.setPlainText("(批量模式不支持修改摘要)")
            self.summary_input.setEnabled(False)
            self.transcript_input.setPlainText("(批量模式不支持修改转录)")
            self.transcript_input.setEnabled(False)
            if hasattr(self, "media_preview"):
                self.media_preview.set_media(None)
            return

        # 单选模式
        self.current_video = video_data
        self.batch_info.setVisible(False)
        self.save_btn.setEnabled(True)
        self.summary_input.setEnabled(True)
        self.transcript_input.setEnabled(True)
        
        self.filename_label.setText(video_data.get("filename", "-"))
        
        # 设置分类
        cat = video_data.get("category", "Other")
        idx = self.category_input.findText(cat)
        if idx >= 0:
            self.category_input.setCurrentIndex(idx)
        else:
            self.category_input.setCurrentText(cat)
            
        # 设置标签及其颜色 (V5.0)
        tags = video_data.get("tags", [])
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except: pass
            
        # 根据 5 分类体系自动分配颜色 (C1-C4 区分)
        colors = {}
        icons = {} # V6.0 Icons support
        cat_color_map = {
            "mood": "#E91E63",    # C1: 粉红
            "subject": "#2196F3", # C2: 蓝色
            "location": "#4CAF50",# C3: 绿色
            "action": "#FF9800",  # C4: 橙色
            "custom": "#757575"   # C5: 灰色
        }
        
        tag_to_cat = {}
        lib_icons = {} # Tag Name -> Icon
        
        if self.service.db:
            tags_detail = self.service.db.get_tags_detail()
            for t in tags_detail:
                lib_icons[t["tag_name"]] = t.get("icon")

        if hasattr(self.service, "tag_config") and self.service.tag_config:
            for group in self.service.tag_config.get("tag_groups", []):
                cat_id = group["id"]
                for t_def in group.get("tags", []):
                    t_name = t_def.get("name") if isinstance(t_def, dict) else t_def
                    tag_to_cat[t_name] = cat_id
                    if isinstance(t_def, dict) and t_def.get("icon"):
                        lib_icons[t_name] = t_def.get("icon")
        
        for t in tags:
            cat_id = tag_to_cat.get(t, "custom")
            colors[t] = cat_color_map.get(cat_id, "#757575")
            icons[t] = lib_icons.get(t)
        
        # 拼写检查：经 service 门面委托 TagProcessor，失败不阻断详情加载
        misspelled_suggestions = {}
        if hasattr(self.service, "check_spelling"):
            misspelled_suggestions = self.service.check_spelling(tags) or {}
        elif hasattr(self.service, "tag_processor"):
            try:
                misspelled_suggestions = self.service.tag_processor.check_spelling(tags) or {}
            except Exception:
                misspelled_suggestions = {}
        
        self.tags_widget.set_tags(tags, colors=colors,
                                 misspelled_tags=list(misspelled_suggestions.keys()),
                                 icons=icons)
        
        # 如果有拼写错误，在标签栏显示一个小提示
        if misspelled_suggestions:
            tips = "拼写纠错建议: " + ", ".join([f"'{k}'->'{v}'" for k, v in misspelled_suggestions.items()])
            self.tags_widget.setToolTip(tips)
        else:
            self.tags_widget.setToolTip("")

        self.summary_input.setPlainText(video_data.get("summary", ""))
        self.transcript_input.setPlainText(video_data.get("transcription", ""))
        
        self.ai_rec_btn.setEnabled(True)
        self.storyboard_btn.setEnabled(True)

        # 素材预览
        path = video_data.get("path")
        if hasattr(self, "media_preview"):
            self.media_preview.set_media(path)

    def save_changes(self):
        """保存修改到数据库"""
        if not self.current_video:
            return
            
        # 获取输入
        new_category = self.category_input.currentText()
        new_tags = self.tags_widget.tags
        merge_tags = self.merge_tags_cb.isChecked()
        
        try:
            if isinstance(self.current_video, list):
                # 批量更新
                for item in self.current_video:
                    updates = {"manual_override": True, "status": "edited"}
                    if new_category:
                        updates["category"] = new_category
                    if new_tags:
                        if merge_tags:
                            existing = set(item.get("tags", []))
                            updates["tags"] = list(existing.union(set(new_tags)))
                        else:
                            updates["tags"] = new_tags
                    
                    self.service.update_video_metadata(item.get("path"), updates)
                QMessageBox.information(self, "成功", f"已批量更新 {len(self.current_video)} 个视频。")
            else:
                # 单个更新
                new_summary = self.summary_input.toPlainText()
                new_transcript = self.transcript_input.toPlainText()
                
                updates = {
                    "category": new_category,
                    "summary": new_summary,
                    "transcription": new_transcript,
                    "manual_override": True,
                    "status": "edited"
                }
                
                if new_tags:
                    if merge_tags:
                        existing = set(self.current_video.get("tags", []))
                        updates["tags"] = list(existing.union(set(new_tags)))
                    else:
                        updates["tags"] = new_tags
                        
                self.service.update_video_metadata(self.current_video.get("path"), updates)
                QMessageBox.information(self, "成功", "视频信息已保存。")
            
            self.data_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败: {str(e)}")

    def refresh_tag_completer(self):
        """刷新标签自动补全列表"""
        # 从设置中获取维度标签
        all_tags = []
        for tags in self.settings.get("tag_dimensions", {}).values():
            all_tags.extend(tags)
        
        # 也可以从数据库获取已有的标签
        self.tags_widget.set_completer(list(set(all_tags)))
