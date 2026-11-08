"""可搜索的候选模型选择框，始终接受用户手填的模型名。"""
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QPushButton, QLabel, QCompleter

from gui.services.model_catalog import model_catalog, connection_identity


class ModelPicker(QWidget):
    textChanged = Signal(str)

    def __init__(self, provider, text="", *, catalog=None, compact=False, parent=None):
        super().__init__(parent)
        self._provider = provider
        self.catalog = catalog or model_catalog()
        self._identity = None
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.setMinimumWidth(100)
        self.combo.setMaxVisibleItems(12)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(8)
        self.combo.lineEdit().setPlaceholderText("选择列表模型，或手动输入自定义模型")
        completer = self.combo.completer()
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.fetch_button = QPushButton("获取" if compact else "获取模型")
        self.fetch_button.setToolTip("获取所选供应商的模型列表；选择后填入，也可直接手动输入。")
        self.fetch_button.clicked.connect(self.fetch_models)
        row.addWidget(self.combo, 1)
        row.addWidget(self.fetch_button)
        layout.addLayout(row)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setVisible(not compact)
        layout.addWidget(self.status)
        self.compact = compact
        self.combo.currentTextChanged.connect(self._text_changed)
        self.catalog.started.connect(self._started)
        self.catalog.completed.connect(self._completed)
        self.setText(text)
        self.refresh_connection()

    def text(self):
        return self.combo.currentText()

    def setText(self, value):
        self.combo.setEditText(value)

    def setPlaceholderText(self, value):
        self.combo.lineEdit().setPlaceholderText(value)

    def _text_changed(self, value):
        if not self._updating:
            self.textChanged.emit(value)

    def _set_models(self, values):
        text = self.text()
        self._updating = True
        blocker = QSignalBlocker(self.combo)
        line_blocker = QSignalBlocker(self.combo.lineEdit())
        self.combo.clear()
        self.combo.addItems(values)
        self.combo.setCurrentIndex(self.combo.findText(text, Qt.MatchExactly))
        self.combo.setEditText(text)
        del line_blocker, blocker
        self._updating = False

    def _snapshot(self):
        try:
            provider = dict(self._provider() or {})
        except (KeyError, RuntimeError) as error:
            raise ValueError("供应商已移除或不可用，请重新选择。") from error
        if not (provider.get("id") or provider.get("provider_id")):
            raise ValueError("请先选择有效的供应商。")
        return {"provider_id": provider.get("provider_id") or provider.get("id"),
                "display_name": provider.get("display_name", ""),
                "api_key": provider.get("api_key", ""), "base_url": provider.get("base_url", "")}

    def refresh_connection(self, *_):
        try:
            provider = self._snapshot()
            key = connection_identity(provider)
        except ValueError:
            provider, key = {}, ""
        if key != self._identity:
            self._identity = key
            self._set_models(self.catalog.cached(provider) or [])
            self._set_status("可获取模型列表，也可直接手动输入。")
        loading = bool(key and self.catalog.is_loading(provider))
        self.fetch_button.setEnabled(not loading)
        self.fetch_button.setText("获取中…" if loading else self._button_text(provider))

    def _button_text(self, provider):
        cached = self.catalog.cached(provider) is not None
        return ("刷新" if cached else "获取") if self.compact else ("刷新列表" if cached else "获取模型")

    def _set_status(self, message):
        self.status.setText(message)
        self.combo.setToolTip(message)
        self.fetch_button.setToolTip(message)

    def fetch_models(self):
        try:
            provider = self._snapshot()
        except ValueError as error:
            self._set_status(str(error))
            return
        self.refresh_connection()
        self.catalog.fetch(provider)

    def _started(self, key):
        self.refresh_connection()
        if key == self._identity:
            self.fetch_button.setEnabled(False)
            self.fetch_button.setText("获取中…")
            self._set_status("正在后台获取模型，可继续手动输入…")

    def _completed(self, key, ok, payload):
        self.refresh_connection()
        if key != self._identity:
            return
        self.fetch_button.setEnabled(True)
        self.fetch_button.setText(self._button_text(self._snapshot()))
        if ok:
            self._set_models(payload)
            self._set_status(f"已获取 {len(payload)} 个模型；展开列表或输入关键词选择，仍可手填。" if payload else "供应商返回空列表，仍可手动填写模型。")
        else:
            self._set_status(str(payload))
            self.fetch_button.setText("重试" if self.compact else "重新获取")
