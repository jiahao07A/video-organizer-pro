# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QListWidget, QListWidgetItem, QGroupBox,
    QFormLayout, QComboBox, QSpinBox, QTextEdit, QMessageBox,
    QTabWidget, QCheckBox, QWidget
)
from PySide6.QtCore import Qt

class TagGroupEditor(QDialog):
    def __init__(self, tag_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("标签组管理")
        self.setMinimumSize(600, 450)
        self.tag_config = tag_config
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        
        # Left: List of groups
        left_layout = QVBoxLayout()
        self.group_list = QListWidget()
        self.group_list.currentRowChanged.connect(self.on_group_selected)
        
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("添加组")
        add_btn.clicked.connect(self.add_group)
        del_btn = QPushButton("删除组")
        del_btn.clicked.connect(self.delete_group)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(del_btn)
        
        move_layout = QHBoxLayout()
        up_btn = QPushButton("↑")
        up_btn.clicked.connect(lambda: self.move_group(-1))
        down_btn = QPushButton("↓")
        down_btn.clicked.connect(lambda: self.move_group(1))
        move_layout.addWidget(up_btn)
        move_layout.addWidget(down_btn)
        
        left_layout.addWidget(QLabel("标签组列表:"))
        left_layout.addWidget(self.group_list)
        left_layout.addLayout(btn_layout)
        left_layout.addLayout(move_layout)
        
        # Right: Group details
        self.detail_group = QGroupBox("组属性编辑")
        self.detail_group.setEnabled(False)
        form = QFormLayout(self.detail_group)
        
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("唯一标识符 (如: mood)")
        self.name_edit = QLineEdit()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["single", "multiple"])
        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 100)
        self.ai_check = QCheckBox("允许 AI 扩展")
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("对此组的 AI 引导指令...")
        
        form.addRow("ID:", self.id_edit)
        form.addRow("显示名称:", self.name_edit)
        form.addRow("选择模式:", self.mode_combo)
        form.addRow("最大数量:", self.max_spin)
        form.addRow("AI 属性:", self.ai_check)
        form.addRow("局部 Prompt:", self.prompt_edit)
        
        save_detail_btn = QPushButton("更新组属性")
        save_detail_btn.clicked.connect(self.save_current_group)
        form.addRow(save_detail_btn)
        
        layout.addLayout(left_layout, 1)
        layout.addWidget(self.detail_group, 2)

    def load_data(self):
        self.group_list.clear()
        for group in self.tag_config.get("tag_groups", []):
            item = QListWidgetItem(f"{group['name']} ({group['id']})")
            item.setData(Qt.UserRole, group["id"])
            self.group_list.addItem(item)

    def on_group_selected(self, row):
        if row < 0:
            self.detail_group.setEnabled(False)
            return
        
        self.detail_group.setEnabled(True)
        group_id = self.group_list.item(row).data(Qt.UserRole)
        group = next(g for g in self.tag_config["tag_groups"] if g["id"] == group_id)
        
        self.id_edit.setText(group["id"])
        self.id_edit.setEnabled(False) # ID usually shouldn't change easily as it's a key
        self.name_edit.setText(group["name"])
        rules = group.get("rules", {})
        self.mode_combo.setCurrentText(rules.get("selection_mode", "single"))
        self.max_spin.setValue(rules.get("max_count", 1))
        self.ai_check.setChecked(rules.get("ai_expandable", False))
        self.prompt_edit.setText(rules.get("local_prompt", ""))

    def add_group(self):
        # 简单添加
        new_id = f"group_{len(self.tag_config.get('tag_groups', [])) + 1}"
        new_group = {
            "id": new_id,
            "name": "新标签组",
            "rules": {
                "selection_mode": "single",
                "max_count": 1,
                "ai_expandable": False,
                "local_prompt": ""
            },
            "tags": []
        }
        self.tag_config.setdefault("tag_groups", []).append(new_group)
        self.load_data()
        self.group_list.setCurrentRow(self.group_list.count() - 1)

    def delete_group(self):
        row = self.group_list.currentRow()
        if row < 0: return
        
        if QMessageBox.warning(self, "确认删除", "删除标签组将导致该组下的标签被移至中转池。确定吗？",
                               QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
            return
            
        group_id = self.group_list.item(row).data(Qt.UserRole)
        self.tag_config["tag_groups"] = [g for g in self.tag_config["tag_groups"] if g["id"] != group_id]
        self.load_data()

    def move_group(self, direction):
        row = self.group_list.currentRow()
        if row < 0: return
        new_row = row + direction
        if 0 <= new_row < self.group_list.count():
            groups = self.tag_config["tag_groups"]
            groups[row], groups[new_row] = groups[new_row], groups[row]
            self.load_data()
            self.group_list.setCurrentRow(new_row)

    def save_current_group(self):
        row = self.group_list.currentRow()
        if row < 0: return
        
        group_id = self.group_list.item(row).data(Qt.UserRole)
        group = next(g for g in self.tag_config["tag_groups"] if g["id"] == group_id)
        
        group["name"] = self.name_edit.text()
        group["rules"] = {
            "selection_mode": self.mode_combo.currentText(),
            "max_count": self.max_spin.value(),
            "ai_expandable": self.ai_check.isChecked(),
            "local_prompt": self.prompt_edit.toPlainText()
        }
        
        # Update list item text
        self.group_list.item(row).setText(f"{group['name']} ({group['id']})")
        QMessageBox.information(self, "已更新", f"组 '{group['name']}' 属性已更新。")

class PromptConfigCenter(QDialog):
    def __init__(self, tag_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prompt 与导出配置中心")
        self.setMinimumSize(700, 500)
        self.tag_config = tag_config
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        
        # Tab 1: Global Prompt
        global_tab = QWidget()
        global_layout = QVBoxLayout(global_tab)
        self.system_prompt_edit = QTextEdit()
        global_layout.addWidget(QLabel("全局系统提示词 (System Prompt):"))
        global_layout.addWidget(self.system_prompt_edit)
        self.tabs.addTab(global_tab, "全局 Prompt")
        
        # Tab 2: Export Schemes
        export_tab = QWidget()
        export_form = QFormLayout(export_tab)
        self.filename_pattern = QLineEdit()
        self.xmp_hierarchical = QCheckBox("启用分层 XMP")
        self.xmp_prefix_category = QCheckBox("XMP 包含分类前缀")
        export_form.addRow("文件名模式:", self.filename_pattern)
        export_form.addRow("XMP 选项:", self.xmp_hierarchical)
        export_form.addRow("", self.xmp_prefix_category)
        self.tabs.addTab(export_tab, "导出配置")
        
        # Tab 3: Categories CRUD
        cat_tab = QWidget()
        cat_layout = QHBoxLayout(cat_tab)
        
        cat_list_layout = QVBoxLayout()
        self.cat_list = QListWidget()
        self.cat_list.currentRowChanged.connect(self.on_cat_selected)
        cat_list_layout.addWidget(QLabel("分类 (Categories):"))
        cat_list_layout.addWidget(self.cat_list)
        
        cat_btn_layout = QHBoxLayout()
        add_cat_btn = QPushButton("添加")
        add_cat_btn.clicked.connect(self.add_category)
        del_cat_btn = QPushButton("删除")
        del_cat_btn.clicked.connect(self.delete_category)
        cat_btn_layout.addWidget(add_cat_btn)
        cat_btn_layout.addWidget(del_cat_btn)
        cat_list_layout.addLayout(cat_btn_layout)
        
        cat_detail_layout = QFormLayout()
        self.cat_id_edit = QLineEdit()
        self.cat_name_edit = QLineEdit()
        self.cat_order_spin = QSpinBox()
        cat_detail_layout.addRow("分类 ID:", self.cat_id_edit)
        cat_detail_layout.addRow("显示名称:", self.cat_name_edit)
        cat_detail_layout.addRow("排序权重:", self.cat_order_spin)
        save_cat_btn = QPushButton("更新分类")
        save_cat_btn.clicked.connect(self.save_current_category)
        cat_detail_layout.addRow(save_cat_btn)
        
        cat_layout.addLayout(cat_list_layout, 1)
        cat_layout.addLayout(cat_detail_layout, 1)
        self.tabs.addTab(cat_tab, "分类管理")
        
        layout.addWidget(self.tabs)
        
        # Bottom Buttons
        bottom_btns = QHBoxLayout()
        save_all_btn = QPushButton("保存全部更改")
        save_all_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom_btns.addStretch()
        bottom_btns.addWidget(save_all_btn)
        bottom_btns.addWidget(cancel_btn)
        layout.addLayout(bottom_btns)

    def load_data(self):
        # Global
        settings = self.tag_config.get("global_settings", {})
        self.system_prompt_edit.setText(settings.get("system_prompt", ""))
        
        # Export
        schemes = settings.get("export_schemes", {})
        self.filename_pattern.setText(schemes.get("filename_pattern", ""))
        self.xmp_hierarchical.setChecked(schemes.get("xmp_hierarchical", True))
        self.xmp_prefix_category.setChecked(schemes.get("xmp_prefix_category", True))
        
        # Categories
        self.refresh_cat_list()

    def refresh_cat_list(self):
        self.cat_list.clear()
        for cat in self.tag_config.get("categories", []):
            item = QListWidgetItem(f"{cat['display_name']} ({cat['id']})")
            item.setData(Qt.UserRole, cat["id"])
            self.cat_list.addItem(item)

    def on_cat_selected(self, row):
        if row < 0: return
        cat_id = self.cat_list.item(row).data(Qt.UserRole)
        cat = next(c for c in self.tag_config["categories"] if c["id"] == cat_id)
        self.cat_id_edit.setText(cat["id"])
        self.cat_id_edit.setEnabled(False)
        self.cat_name_edit.setText(cat["display_name"])
        self.cat_order_spin.setValue(cat.get("order", 0))

    def add_category(self):
        new_id = f"cat_{len(self.tag_config.get('categories', [])) + 1}"
        new_cat = {
            "id": new_id,
            "display_name": "新分类",
            "order": len(self.tag_config.get("categories", []))
        }
        self.tag_config.setdefault("categories", []).append(new_cat)
        self.refresh_cat_list()
        self.cat_list.setCurrentRow(self.cat_list.count() - 1)

    def delete_category(self):
        row = self.cat_list.currentRow()
        if row < 0: return
        if QMessageBox.warning(self, "确认删除", "确定删除此分类吗？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
            return
        cat_id = self.cat_list.item(row).data(Qt.UserRole)
        self.tag_config["categories"] = [c for c in self.tag_config["categories"] if c["id"] != cat_id]
        self.refresh_cat_list()

    def save_current_category(self):
        row = self.cat_list.currentRow()
        if row < 0: return
        cat_id = self.cat_list.item(row).data(Qt.UserRole)
        cat = next(c for c in self.tag_config["categories"] if c["id"] == cat_id)
        cat["display_name"] = self.cat_name_edit.text()
        cat["order"] = self.cat_order_spin.value()
        self.refresh_cat_list()
        QMessageBox.information(self, "已更新", "分类属性已更新。")

    def accept(self):
        # Collect all data back to tag_config
        settings = self.tag_config.setdefault("global_settings", {})
        settings["system_prompt"] = self.system_prompt_edit.toPlainText()
        
        schemes = settings.setdefault("export_schemes", {})
        schemes["filename_pattern"] = self.filename_pattern.text()
        schemes["xmp_hierarchical"] = self.xmp_hierarchical.isChecked()
        schemes["xmp_prefix_category"] = self.xmp_prefix_category.isChecked()
        
        super().accept()
