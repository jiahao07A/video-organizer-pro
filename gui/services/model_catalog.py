"""按连接身份隔离的会话内模型候选；后台线程不持有表单或行控件。"""
import copy
import hashlib
import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from core.ai_gateway import AiRequestGateway
from gui.workers.io_worker import IoWorker


def connection_identity(provider):
    values = (str(provider.get("provider_id") or provider.get("id") or ""),
              str(provider.get("base_url") or "").strip(), str(provider.get("api_key") or ""))
    return hashlib.sha256(json.dumps(values).encode("utf-8")).hexdigest()


class ModelCatalog(QObject):
    started = Signal(str)
    completed = Signal(str, bool, object)

    def __init__(self, parent=None, *, gateway=None):
        super().__init__(parent)
        self.gateway = gateway or AiRequestGateway({}, timeout=15)
        self._cache = {}
        self._workers = {}
        self._closing = False

    def cached(self, provider):
        values = self._cache.get(connection_identity(provider))
        return list(values) if values is not None else None

    def is_loading(self, provider):
        return connection_identity(provider) in self._workers

    def fetch(self, provider):
        key = connection_identity(provider)
        if self._closing or key in self._workers:
            return
        snapshot = copy.deepcopy(provider)
        worker = IoWorker(lambda: self.gateway.list_models(route_override=snapshot), self)
        self._workers[key] = worker
        outcome = [False, "获取模型列表未完成，请重试或手动填写。"]

        def complete(ok, payload):
            outcome[:] = [ok, payload]

        def finished():
            self._workers.pop(key, None)
            ok, payload = outcome
            if ok:
                self._cache[key] = list(payload)
            if not self._closing:
                self.completed.emit(key, ok, payload)
            worker.deleteLater()

        worker.task_finished.connect(complete)
        # 在QThread真正结束之后清理，允许弹窗关闭或备用行移除后安全返回。
        worker.finished.connect(finished)
        self.started.emit(key)
        worker.start()

    def shutdown(self):
        self._closing = True
        for worker in list(self._workers.values()):
            worker.wait()


def model_catalog():
    app = QApplication.instance()
    catalog = getattr(app, "_model_catalog", None)
    if catalog is None:
        catalog = ModelCatalog(app)
        app._model_catalog = catalog
        app.aboutToQuit.connect(catalog.shutdown)
    return catalog
