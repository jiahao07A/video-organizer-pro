# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QFileDialog, QTextEdit, 
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QComboBox
)
from PySide6.QtCore import Qt, QThread, Signal
from core.tag_import_service import TagImportService

class AIClassifyThread(QThread):
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, service: TagImportService, tags):
        super().__init__()
        self.service = service
        self.tags = tags

    def run(self):
        try:
            result = self.service.classify_tags_with_ai(self.tags)
            self.finished_signal.emit(result)
        except Exception as e:
            self.error_signal.emit(str(e))

class TagImportWizard(QWizard):
    def __init__(self, import_service: TagImportService, parent=None):
        super().__init__(parent)
        self.import_service = import_service
        self.imported_tags = []
        self.classified_data = {} # {tag_name: category_id}
        
        self.setWindowTitle("标签导入向导")
        self.resize(600, 500)
        
        # 定义维度选项用于校对页
        self.dimensions = [
            ("pool", "中转池"),
            ("mood", "C1: 氛围"),
            ("subject", "C2: 主体"),
            ("location", "C3: 场景"),
            ("action", "C4: 动作"),
            ("custom", "C5: 建议")
        ]
        
        self.addPage(FileSelectionPage(self))
        self.addPage(AIProcessingPage(self))
        self.addPage(ReviewPage(self))
        
        self.setButtonText(QWizard.NextButton, "下一步 >")
        self.setButtonText(QWizard.BackButton, "< 上一步")
        self.setButtonText(QWizard.FinishButton, "完成并入库")
        self.setButtonText(QWizard.CancelButton, "取消")

class FileSelectionPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("第 1 步: 选择文件与预览")
        self.setSubTitle("请选择包含标签的 .txt 文件（每行一个标签）。")
        
        layout = QVBoxLayout(self)
        
        file_layout = QHBoxLayout()
        self.path_label = QLabel("未选择文件")
        self.path_label.setStyleSheet("color: #8C8C8C;")
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(self.path_label, 1)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)
        
        layout.addWidget(QLabel("预览内容:"))
        self.preview_edit = QTextEdit()
        self.preview_edit.setReadOnly(True)
        layout.addWidget(self.preview_edit)
        
        self.registerField("file_path*", self.path_label, "text")

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择标签文件", "", "Text Files (*.txt)")
        if file_path:
            self.path_label.setText(file_path)
            tags = self.wizard().import_service.read_tags_from_file(file_path)
            self.wizard().imported_tags = tags
            self.preview_edit.setPlainText("\n".join(tags))
            self.completeChanged.emit()

    def isComplete(self):
        return len(self.wizard().imported_tags) > 0

class AIProcessingPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("第 2 步: AI 智能分类")
        self.setSubTitle("正在利用 AI 对标签进行语义分析和归类...")
        
        layout = QVBoxLayout(self)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # 忙碌状态
        layout.addStretch()
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("准备执行...")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        layout.addStretch()
        
        self._is_finished = False

    def initializePage(self):
        self._is_finished = False
        self.status_label.setText(f"正在对 {len(self.wizard().imported_tags)} 个标签进行分类...")
        
        self.thread = AIClassifyThread(self.wizard().import_service, self.wizard().imported_tags)
        self.thread.finished_signal.connect(self.on_finished)
        self.thread.error_signal.connect(self.on_error)
        self.thread.start()

    def on_finished(self, result):
        # 转换结果格式为 {tag: cat_id} 方便校对页展示
        flat_data = {}
        for cat_id, tags in result.items():
            for t in tags:
                flat_data[t] = cat_id
        
        # 补全未被 AI 分类的标签到 pool
        for t in self.wizard().imported_tags:
            if t not in flat_data:
                flat_data[t] = "pool"
                
        self.wizard().classified_data = flat_data
        self._is_finished = True
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText("分类完成！点击下一步校对结果。")
        self.completeChanged.emit()

    def on_error(self, err):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label.setText(f"发生错误: {err}")
        QMessageBox.critical(self, "AI 分类失败", f"错误详情: {err}")

    def isComplete(self):
        return self._is_finished

class ReviewPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("第 3 步: 结果校对")
        self.setSubTitle("请检查 AI 的分类结果，如有误可手动调整。")
        
        layout = QVBoxLayout(self)
        
        # 工具栏
        tool_layout = QHBoxLayout()
        move_pool_btn = QPushButton("移动选中到中转池")
        move_pool_btn.clicked.connect(self.move_selected_to_pool)
        tool_layout.addStretch()
        tool_layout.addWidget(move_pool_btn)
        layout.addLayout(tool_layout)
        
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["标签名", "分类"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

    def move_selected_to_pool(self):
        selected_ranges = self.table.selectedRanges()
        if not selected_ranges:
            return
            
        for r in selected_ranges:
            for row in range(r.topRow(), r.bottomRow() + 1):
                combo = self.table.cellWidget(row, 1)
                if isinstance(combo, QComboBox):
                    # 寻找 "pool" 的索引
                    for i in range(combo.count()):
                        if combo.itemData(i) == "pool":
                            combo.setCurrentIndex(i)
                            break

    def initializePage(self):
        data = self.wizard().classified_data
        self.table.setRowCount(len(data))
        
        for i, (tag, cat_id) in enumerate(data.items()):
            self.table.setItem(i, 0, QTableWidgetItem(tag))
            
            combo = QComboBox()
            for cid, cname in self.wizard().dimensions:
                combo.addItem(cname, cid)
                if cid == cat_id:
                    combo.setCurrentIndex(combo.count() - 1)
            
            self.table.setCellWidget(i, 1, combo)

    def validatePage(self):
        # 最终收集数据回 wizard
        final_result = {}
        for i in range(self.table.rowCount()):
            tag = self.table.item(i, 0).text()
            cat_id = self.table.cellWidget(i, 1).currentData()
            if cat_id not in final_result:
                final_result[cat_id] = []
            final_result[cat_id].append(tag)
            
        self.wizard().final_classified = final_result
        return True
