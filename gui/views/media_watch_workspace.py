"""素材监控工作区：任务、基础配置、批次进度与人工接管。"""
import os
import uuid

from PySide6.QtCore import Qt, Signal, QTimer, QUrl, QItemSelectionModel
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QComboBox, QProgressBar,
    QFileDialog, QInputDialog, QMessageBox, QDialog, QDialogButtonBox, QCheckBox,
    QFormLayout, QGroupBox, QSizePolicy,
)
from core.watch_config import effective_directory_options
from core.media_registry import management_label, path_key
from .media_watch_advanced import load_watch_config, save_watch_config
from ..workers.io_worker import run_io_job


STAGES = {
    'queued': '等待分析', 'pending': '等待手动分析', 'analyzing': '分析中',
    'duplicate': '重复跳过', 'review': '待人工处理', 'rename_ready': '等待重命名',
    'renaming': '重命名中', 'xmp_ready': '等待XMP', 'xmp': '写入XMP',
    'done': '已完成', 'failed': '失败', 'cancelled': '已取消',
    'ignored': '已忽略', 'manual': '已交工作台', 'superseded': '内容已更新',
}


class MediaWatchWorkspace(QWidget):
    changed = Signal()
    open_advanced = Signal(str)
    work_scope_changed = Signal()
    status_message = Signal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.monitor = service.media_monitor
        self.registry = service.media_registry
        self.task_id = ''
        self._loading = True
        self._rows = []
        self._config_signature = None
        self._offset = 0
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel('素材监控')
        title.setStyleSheet('font-size: 24px; font-weight: 600;')
        heading.addWidget(title)
        heading.addStretch()
        for text, handler in [('添加目录', self.add_directory), ('高级设置', lambda: self.open_advanced.emit(self.task_id)),
                              ('暂停全部', self.pause_all), ('恢复全部', self.resume_all)]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            heading.addWidget(button)
        layout.addLayout(heading)
        hint = QLabel('自动发现、入库和分析；重复副本只登记和标记。监控不会改变当前工作台范围。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks = QTableWidget(0, 3)
        self.tasks.setHorizontalHeaderLabels(['任务', '目录', '状态'])
        self.tasks.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tasks.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tasks.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tasks.horizontalHeader().setStretchLastSection(True)
        self.tasks.itemSelectionChanged.connect(self._select_task)
        left_layout.addWidget(self.tasks)
        row = QHBoxLayout()
        for text, handler in [('改名', self.rename_task), ('换目录', self.change_path), ('移除', self.remove_task)]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        left_layout.addLayout(row)
        splitter.addWidget(left)
        right = QWidget()
        content = QVBoxLayout(right)
        content.setContentsMargins(0, 0, 0, 0)
        group = QGroupBox('基础配置 · 未覆盖的选项沿用全局默认')
        form = QFormLayout(group)
        self.basics = {}
        for key, label in [('recursive', '递归扫描'), ('auto_analyze', '自动分析'), ('auto_rename', '自动重命名')]:
            combo = QComboBox()
            combo.addItem('继承默认', None)
            combo.addItem('开启', True)
            combo.addItem('关闭', False)
            combo.currentIndexChanged.connect(self._save_basics)
            self.basics[key] = combo
            form.addRow(label, combo)
        self.preset = QComboBox()
        self.preset.currentIndexChanged.connect(self._save_basics)
        form.addRow('命名方案', self.preset)
        self.effective_label = QLabel()
        self.effective_label.setWordWrap(True)
        self.effective_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        form.addRow(self.effective_label)
        content.addWidget(group)
        controls = QHBoxLayout()
        for text, handler in [('暂停任务', lambda: self.pause_task(True)), ('恢复任务', lambda: self.pause_task(False)),
                              ('立即补扫', lambda: self.scan(False)), ('处理历史素材', lambda: self.scan(True)),
                              ('取消当前批次', self.monitor.cancel_current_batch)]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            controls.addWidget(button)
        content.addLayout(controls)
        self.status_label = QLabel('等待素材变化')
        self.status_label.setWordWrap(True)
        content.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.sub_progress = QProgressBar()
        self.progress.setFormat('本批次 %v/%m')
        self.sub_progress.setFormat('分析本轮 %p%')
        content.addWidget(self.progress)
        content.addWidget(self.sub_progress)
        filter_row = QHBoxLayout()
        self.stage_filter = QComboBox()
        self.stage_filter.addItem('全部处理状态', '')
        for value, label in STAGES.items():
            self.stage_filter.addItem(label, value)
        self.stage_filter.currentIndexChanged.connect(lambda: self._page(0, reset=True))
        filter_row.addWidget(self.stage_filter)
        self.counts = QLabel()
        self.counts.setWordWrap(True)
        filter_row.addWidget(self.counts, 1)
        content.addLayout(filter_row)
        pages = QHBoxLayout()
        for text, handler in [('上一页', lambda: self._page(-1)), ('下一页', lambda: self._page(1)),
                              ('补齐历史指纹', lambda: self._job(lambda: self.registry.index_existing(force=True)))]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            pages.addWidget(button)
        self.page_label = QLabel()
        pages.addWidget(self.page_label)
        pages.addStretch()
        content.addLayout(pages)
        self.items = QTableWidget(0, 5)
        self.items.setHorizontalHeaderLabels(['素材', '处理阶段', '来源／重复标记', '批次', '详情'])
        self.items.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.items.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.items.horizontalHeader().setStretchLastSection(True)
        self.items.setColumnWidth(0, 220)
        self.items.setColumnWidth(2, 240)
        content.addWidget(self.items, 1)
        for actions in [
            [('重试／开始处理', self.retry_items), ('忽略', lambda: self.set_items('ignored')),
             ('恢复处理', lambda: self.set_items('queued')), ('确认新内容', self.accept_changes),
             ('标记已核对', self.mark_reviewed)],
            [('命名预览／执行', self.rename_items), ('回滚所选批次', self.rollback_batch),
             ('同步XMP', self.sync_xmp), ('导出ALE', lambda: self.export_items('ale')),
             ('导出FCPX', lambda: self.export_items('fcpx'))],
            [('打开目录', self.open_folder), ('加入工作台并接管', self.take_over)],
        ]:
            row = QHBoxLayout()
            for text, handler in actions:
                button = QPushButton(text)
                button.clicked.connect(handler)
                row.addWidget(button)
            row.addStretch()
            content.addLayout(row)
        splitter.addWidget(right)
        splitter.setSizes([330, 950])
        layout.addWidget(splitter, 1)
        self._loading = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start()
        self.refresh()

    def _task(self):
        return next((task for task in load_watch_config(self.service)['directories'] if task['id'] == self.task_id), None)

    def _save(self, config):
        try:
            save_watch_config(self.service, config)
        except Exception as exc:
            QMessageBox.warning(self, '保存监控任务失败', str(exc))
            return False
        self.changed.emit()
        self._config_signature = None
        self.refresh()
        return True

    def _select_task(self):
        row = self.tasks.currentRow()
        item = self.tasks.item(row, 0)
        self.task_id = item.data(Qt.UserRole) if item else ''
        self._offset = 0
        self._populate_basics()
        self.refresh()

    def _populate_basics(self):
        config = load_watch_config(self.service)
        task = self._task()
        self._loading = True
        for key, combo in self.basics.items():
            combo.setEnabled(True)
            combo.setItemText(0, '继承默认（' + ('开启' if config[key] else '关闭') + '）')
            combo.setCurrentIndex(max(0, combo.findData(task.get(key) if task else config[key])))
        self.preset.clear()
        self.preset.addItem('继承命名设置', None)
        self.preset.addItem('使用自定义模板（高级设置）', '')
        for name in self.service.tag_config.get('global_settings', {}).get('presets', {}):
            self.preset.addItem(name, name)
        self.preset.setCurrentIndex(max(0, self.preset.findData(task.get('rename_preset') if task else config['rename_preset'])))
        self.preset.setEnabled(True)
        if task:
            effective = effective_directory_options(config, task['path'])
            self.effective_label.setText(task['path'] + ('\n自动分析：' + ('开' if effective['auto_analyze'] else '关') + ' · 自动重命名：' + ('开' if effective['auto_rename'] else '关') if effective else '\n任务已停用'))
        else:
            self.effective_label.setText('全局基础默认：后续新任务及未覆盖的任务采用这些设置。')
        self._loading = False

    def _save_basics(self, *_):
        if self._loading:
            return
        config = load_watch_config(self.service)
        task = next((task for task in config['directories'] if task['id'] == self.task_id), config)
        for key, combo in self.basics.items():
            if task is not config or combo.currentData() is not None:
                task[key] = combo.currentData()
        task['rename_preset'] = self.preset.currentData() if task is not config else self.preset.currentData() or ''
        self._save(config)

    def refresh(self, *_):
        config = load_watch_config(self.service)
        signature = repr(config)
        if signature != self._config_signature:
            self.tasks.blockSignals(True)
            self.tasks.setRowCount(len(config['directories']) + 1)
            default = {'id': '', 'name': '全局默认', 'path': '基础配置 · 新任务继承'}
            displayed = [default] + config['directories']
            for index, task in enumerate(displayed):
                for column, value in enumerate([task['name'], task['path'], '']):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, task['id'])
                    item.setToolTip(task['path'])
                    self.tasks.setItem(index, column, item)
            selected = next((i for i,t in enumerate(displayed) if t['id'] == self.task_id), 0)
            self.task_id = displayed[selected]['id']
            self.tasks.selectRow(selected)
            self.tasks.blockSignals(False)
            self._config_signature = signature
            self._populate_basics()
        for index, task in enumerate(config['directories'], 1):
            state = self.registry.state(task['id'])
            text = '已停用' if not task['enabled'] else '已暂停' if state['paused'] or self.monitor._paused_all else '目录离线' if not os.path.isdir(task['path']) else '监控中' if self.monitor.running else '尚未启动'
            self.tasks.item(index, 2).setText(text)
        selected_ids = {row['id'] for row in self.selected_items(expand=False)}
        stage = self.stage_filter.currentData()
        self._rows = self.registry.items(self.task_id, limit=200, offset=self._offset, stage=stage) if self.task_id else []
        self.page_label.setText(f'第 {self._offset // 200 + 1} 页 · 本页 {len(self._rows)} 项')
        self.items.setUpdatesEnabled(False)
        self.items.clearSelection()
        self.items.setRowCount(len(self._rows))
        task_names = {task['id']: task['name'] for task in config['directories']}
        for index, row in enumerate(self._rows):
            label = management_label(row, task_names)
            for column, value in enumerate([row['filename'], STAGES.get(row['stage'], row['stage']), label, row['batch_id'][:8], row['error'] or row['path']]):
                item = QTableWidgetItem(str(value or ''))
                item.setToolTip(str(value or ''))
                self.items.setItem(index, column, item)
            if row['id'] in selected_ids:
                self.items.selectionModel().select(self.items.model().index(index, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.items.setUpdatesEnabled(True)
        counts = self.service.db.execute_query('SELECT stage,COUNT(*) AS n FROM media_watch_items WHERE task_id=? GROUP BY stage', (self.task_id,))
        self.counts.setText(' · '.join(f"{STAGES.get(row['stage'],row['stage'])} {row['n']}" for row in counts))
        discovery = self.monitor.snapshot(self.task_id).get('discovery', {})
        if discovery:
            self.counts.setText(f"扫描 {discovery.get('scanned',0)} · 指纹 {discovery.get('verified',0)} · 等待稳定 {discovery.get('waiting',0)} · " + self.counts.text())
        batches = self.service.db.execute_query('SELECT snapshot FROM media_watch_batches WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (self.task_id,))
        if batches:
            import json
            snap = json.loads(batches[0]['snapshot'])
            total = int(snap.get('overall_total') or 0)
            done = int(snap.get('overall_done') or 0)
            self.progress.setRange(0, max(1,total))
            self.progress.setValue(done)
            self.sub_progress.setValue(int(snap.get('round_percent') or snap.get('sub_percent') or 0))
            self.status_label.setText(snap.get('message') or '等待处理')
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.sub_progress.setValue(0)
            self.status_label.setText('等待素材变化；文件稳定后自动入库')

    def _page(self, direction, reset=False):
        if direction > 0 and len(self._rows) < 200:
            return
        self._offset = 0 if reset else max(0, self._offset + direction * 200)
        self.refresh()

    def add_directory(self):
        path = QFileDialog.getExistingDirectory(self, '添加素材监控目录')
        if not path:
            return
        config = load_watch_config(self.service)
        if any(path_key(task['path']) == path_key(path) for task in config['directories']):
            QMessageBox.information(self, '目录已存在', '该目录已经登记。')
            return
        mode = config['initial_scan_mode']
        if config['ask_initial_scan']:
            dialog = QDialog(self)
            dialog.setWindowTitle('首次扫描范围')
            form = QVBoxLayout(dialog)
            choice = QComboBox()
            choice.addItem('处理全部既有视频', 'all')
            choice.addItem('仅处理后续新增', 'new')
            choice.setCurrentIndex(max(0, choice.findData(mode)))
            form.addWidget(choice)
            remember = QCheckBox('保存为默认，下次不再询问')
            form.addWidget(remember)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addWidget(buttons)
            if dialog.exec() != QDialog.Accepted:
                return
            mode = choice.currentData()
            if remember.isChecked():
                config['initial_scan_mode'], config['ask_initial_scan'] = mode, False
        self.task_id = uuid.uuid4().hex
        config['directories'].append({'id': self.task_id, 'name': os.path.basename(path) or path, 'path': path,
                                      'enabled': True, 'initial_scan_mode': mode})
        self._save(config)

    def rename_task(self):
        task = self._task()
        if task:
            name, ok = QInputDialog.getText(self, '修改监控任务名称', '显示名称（不改文件夹名）', text=task['name'])
            if ok and name.strip():
                config = load_watch_config(self.service)
                next(t for t in config['directories'] if t['id'] == self.task_id)['name'] = name.strip()
                self._save(config)

    def change_path(self):
        if not self._task():
            return
        path = QFileDialog.getExistingDirectory(self, '更换监控路径；保留原素材和历史')
        if path:
            config = load_watch_config(self.service)
            if any(t['id'] != self.task_id and path_key(t['path']) == path_key(path) for t in config['directories']):
                QMessageBox.warning(self, '目录已存在', '该目录已由另一个任务监控。')
                return
            next(t for t in config['directories'] if t['id'] == self.task_id)['path'] = path
            self.service.db.execute_non_query('UPDATE media_watch_state SET baseline_done=0 WHERE task_id=?', (self.task_id,))
            self.service.db.execute_non_query('DELETE FROM media_watch_baseline WHERE task_id=?', (self.task_id,))
            self._save(config)

    def remove_task(self):
        if not self._task():
            return
        config = load_watch_config(self.service)
        config['directories'] = [t for t in config['directories'] if t['id'] != self.task_id]
        self._save(config)

    def pause_all(self):
        self.monitor.pause()
        self.refresh()

    def resume_all(self):
        self.monitor.resume()
        self.refresh()

    def pause_task(self, paused):
        if self.task_id:
            self.monitor.pause_task(self.task_id, paused)
            self.refresh()

    def scan(self, historical):
        self.monitor.request_scan(self.task_id, include_history=historical)
        self.status_label.setText('已安排后台补扫' + ('，包括历史素材' if historical else ''))

    def selected_items(self, expand=True):
        indices = {index.row() for index in self.items.selectionModel().selectedRows()} if hasattr(self, 'items') else set()
        return [self._rows[index] for index in sorted(indices) if index < len(self._rows)] if indices else list(self._rows) if expand else []

    def _paths(self):
        rows = list(getattr(self, '_operation_rows', []))
        if any(row['stage'] in ('analyzing','renaming','xmp') for row in rows):
            raise ValueError('所选素材仍在处理中，请先取消当前批次并等待收尾。')
        paths = self.service.filter_processable_paths([row['path'] for row in rows])
        if not paths:
            raise ValueError('没有可处理素材：重复副本和内容变化待核对素材已跳过。')
        return paths

    def _job(self, fn, on_ok=None):
        operation_rows = [dict(row) for row in self.selected_items()]
        run_io_job(self, fn=fn, on_ok=on_ok or (lambda result: self.refresh()),
            on_start=lambda: setattr(self, '_operation_rows', operation_rows),
            busy_message='正在处理所选素材…')

    def set_items(self, stage):
        rows = self.selected_items()
        if any(row['stage'] in ('analyzing','renaming','xmp') for row in rows):
            QMessageBox.information(self, '任务仍在运行', '请先取消当前批次，等待收尾后再操作。')
            return
        self.registry.reset_items([row['id'] for row in rows if not row.get('duplicate_of')], stage)
        self.monitor._wake.set()
        self.refresh()

    def retry_items(self):
        rows = self.selected_items()
        for row in rows:
            if not row.get('duplicate_of') and row['stage'] not in ('analyzing','renaming','xmp','review'):
                self.registry.reset_items([row['id']], row['stage'] if row['stage'] in ('xmp_ready','rename_ready') else 'queued')
        self.monitor._wake.set()
        self.refresh()

    def accept_changes(self):
        operation_rows = [dict(row) for row in self.selected_items()
            if row['stage'] == 'review' and not row.get('duplicate_of')]
        paths = [row['path'] for row in operation_rows]
        if paths and QMessageBox.question(self, '确认使用新内容', '保留旧内容快照，清除这些素材的旧分析字段并允许重新分析？') == QMessageBox.Yes:
            def complete(_):
                self.registry.reset_items([row['id'] for row in operation_rows], 'queued')
                self.monitor._wake.set()
                self.refresh()
            self._job(lambda: self.registry.accept_changes(paths), complete)

    def mark_reviewed(self):
        self.registry.mark_reviewed([row['path'] for row in self.selected_items()])
        self.refresh()

    def rename_items(self):
        from ..widgets.rename_dialog import BatchRenameDialog
        def show(paths):
            if not paths:
                self.status_label.setText('没有可处理素材；重复副本已跳过。')
                return
            dialog = BatchRenameDialog(self.service, self.service.db.get_videos_by_paths(paths), self)
            if dialog.exec() == QDialog.Accepted:
                config = dialog.get_final_config()
                selected = dialog.selected_source_paths()
                self._job(lambda: self.service.batch_rename(selected_paths=selected, custom_pattern=config['pattern'], regex_find=config['find_regex'], regex_replace=config['replace_str'], date_value=config['preview_date']))
        self._job(self._paths, show)

    def rollback_batch(self):
        ids = list(dict.fromkeys(row['batch_id'] for row in self.selected_items()))
        if len(ids) != 1:
            QMessageBox.information(self, '选择一个批次', '请选中同一批次内的素材。')
            return
        rows = self.service.db.execute_query('SELECT rename_session_id FROM media_watch_batches WHERE id=?', (ids[0],))
        session = rows[0]['rename_session_id'] if rows else None
        if session and QMessageBox.question(self, '回滚重命名', '仅回滚所选批次的重命名，不回滚其他批次，是否继续？') == QMessageBox.Yes:
            self._job(lambda: self.service.rollback_last_session(session))

    def sync_xmp(self):
        self._job(lambda: self.service.sync_metadata_to_xmp(self._paths(), return_details=True))

    def export_items(self, kind):
        path, _ = QFileDialog.getSaveFileName(self, '导出所选素材', '', 'ALE (*.ale)' if kind == 'ale' else 'FCPX XML (*.fcpxml)')
        if path:
            method = self.service.export_to_ale if kind == 'ale' else self.service.export_to_fcpx_xml
            self._job(lambda: method(path, self._paths()))

    def open_folder(self):
        rows = self.selected_items()
        path = os.path.dirname(rows[0]['path']) if rows else (self._task() or {}).get('path')
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def take_over(self):
        operation_rows = [dict(row) for row in self.selected_items()]
        def complete(paths):
            if paths:
                self.service.append_work_scope(paths, scan=False)
                self.registry.reset_items([row['id'] for row in operation_rows if row['path'] in paths], 'manual')
                self.work_scope_changed.emit()
                self.status_message.emit(f'已将 {len(paths)} 项交给工作台；后台停止派发这些素材。')
        self._job(self._paths, complete)
