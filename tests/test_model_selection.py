"""模型发现、显式选择与自定义输入：使用模拟列表接口和真实Qt后台线程。"""
import copy
import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core.ai_gateway import AiRequestGateway
from core.video_organizer_service import SettingsManager
from gui.services.model_catalog import ModelCatalog
from gui.widgets.model_picker import ModelPicker
from gui.widgets.recovery_controls import RecoveryPolicyEditor, ManualRecoveryDialog
from gui.views.settings import SettingsView
from tests.test_http400_recovery import service, video, fallback


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def provider(**values):
    return {"provider_id": "main", "api_key": "secret-main", "base_url": "https://main.invalid/v1", **values}


class Gateway:
    def __init__(self, fn=lambda route: ["model-a", "vision-b"]):
        self.fn = fn
        self.calls = []

    def list_models(self, *, route_override):
        self.calls.append(copy.deepcopy(route_override))
        return self.fn(route_override)


def test_gateway_normalizes_model_ids_and_disables_sdk_retries(monkeypatch):
    options = []
    response = SimpleNamespace(data=[SimpleNamespace(id="vision-b"), {"id": "model-a"}, "model-a", {}, {"id": None}, " "])
    client = SimpleNamespace(models=SimpleNamespace(list=lambda: response))
    monkeypatch.setattr("core.ai_gateway.OpenAI", lambda **kw: options.append(kw) or client)
    gateway = AiRequestGateway({}, timeout=15)
    assert gateway.list_models(route_override=provider()) == ["model-a", "vision-b"]
    assert options[0]["max_retries"] == 0 and options[0]["timeout"] == 15
    client.models.list = lambda: {"data": []}
    assert gateway.list_models(route_override=provider()) == []
    client.models.list = lambda: {"data": None}
    with pytest.raises(RuntimeError, match="格式无法识别"):
        gateway.list_models(route_override=provider())


@pytest.mark.parametrize("code,expected", [(401, "鉴权"), (403, "鉴权"), (404, "不提供"), (405, "不提供"), (501, "不提供"), (429, "频繁"), (500, "HTTP 500"), (None, "网络")])
def test_gateway_model_errors_are_safe_and_actionable(monkeypatch, code, expected):
    error = RuntimeError("connection failed secret-main https://private.invalid/api?api_key=SECRET")
    error.status_code = code
    def fail():
        raise error
    gateway = AiRequestGateway({})
    monkeypatch.setattr(gateway, "client_for_route", lambda route: SimpleNamespace(models=SimpleNamespace(list=fail)))
    with pytest.raises(RuntimeError) as caught:
        gateway.list_models(route_override=provider())
    assert expected in str(caught.value)
    assert "secret-main" not in str(caught.value) and "private.invalid" not in str(caught.value)


def test_fetch_deduplicates_preserves_input_and_only_selection_changes_model(app):
    gate = threading.Event()
    def delayed(route):
        assert gate.wait(3)
        return ["model-a", "vision-b"]
    gateway = Gateway(delayed)
    catalog = ModelCatalog(gateway=gateway)
    first = ModelPicker(provider, "custom-before", catalog=catalog)
    second = ModelPicker(provider, "other-custom", catalog=catalog)
    changes = []
    first.textChanged.connect(changes.append)
    first.fetch_models()
    first.fetch_models()
    second.fetch_models()
    assert not first.fetch_button.isEnabled()
    first.setText("custom-during-fetch")
    gate.set()
    wait_for(app, lambda: not catalog._workers)
    assert len(gateway.calls) == 1
    assert first.text() == "custom-during-fetch" and second.text() == "other-custom"
    assert changes == ["custom-during-fetch"]
    first.combo.completer().setCompletionPrefix("vision")
    assert first.combo.completer().completionCount() == 1
    first.combo.setCurrentIndex(first.combo.findText("vision-b"))
    assert first.text() == "vision-b" and changes[-1] == "vision-b"
    first.setText("custom/not-in-list")
    assert first.text() == "custom/not-in-list"


@pytest.mark.parametrize("field,value", [("provider_id", "backup"), ("api_key", "changed-key"), ("base_url", "https://new.invalid/v1")])
def test_connection_changes_ignore_old_response_and_isolate_cache(app, field, value):
    old = provider()
    current = dict(old)
    gate = threading.Event()
    def load(route):
        if route[field] == old[field]:
            assert gate.wait(3)
            return ["old-model"]
        return ["new-model"]
    gateway = Gateway(load)
    catalog = ModelCatalog(gateway=gateway)
    picker = ModelPicker(lambda: current, "keep-custom", catalog=catalog)
    picker.fetch_models()
    current[field] = value
    picker.refresh_connection()
    assert picker.combo.count() == 0
    picker.fetch_models()
    wait_for(app, lambda: picker.combo.count() == 1)
    assert picker.combo.itemText(0) == "new-model"
    gate.set()
    wait_for(app, lambda: not catalog._workers)
    assert picker.combo.itemText(0) == "new-model" and picker.text() == "keep-custom"
    current.update(old)
    picker.refresh_connection()
    assert picker.combo.itemText(0) == "old-model"
    assert len(gateway.calls) == 2


def test_failed_or_empty_fetch_keeps_manual_value(app):
    values = iter([["first-model"], RuntimeError("接口暂不可用，可手动填写。"), []])
    def load(route):
        result = next(values)
        if isinstance(result, Exception):
            raise result
        return result
    catalog = ModelCatalog(gateway=Gateway(load))
    picker = ModelPicker(provider, "custom", catalog=catalog)
    for expected in ("已获取", "接口暂不可用", "空列表"):
        picker.fetch_models()
        wait_for(app, lambda: not catalog._workers)
        assert expected in picker.status.text()
        assert picker.text() == "custom"
    assert picker.combo.count() == 0 and picker.combo.isEditable()


def test_removed_recovery_row_does_not_destroy_running_worker(app, service, monkeypatch):
    gate = threading.Event()
    catalog = ModelCatalog(gateway=Gateway(lambda route: gate.wait(3) and ["model-a"]))
    monkeypatch.setattr("gui.widgets.model_picker.model_catalog", lambda: catalog)
    editor = RecoveryPolicyEditor(service.settings, {"fallbacks": [fallback()]})
    table = editor.fields["fallbacks"]
    picker = table.cellWidget(0, 2)
    picker.fetch_models()
    table.selectRow(0)
    editor.remove_row("fallbacks")
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(picker)
    gate.set()
    wait_for(app, lambda: not catalog._workers)
    assert catalog.cached(provider(provider_id="backup", api_key="secret-backup", base_url="https://backup.invalid/v1")) == ["model-a"]


def test_main_settings_fetch_uses_unsaved_connection_without_saving_or_dirtying(app, service, tmp_path, monkeypatch):
    gateway = Gateway()
    catalog = ModelCatalog(gateway=gateway)
    monkeypatch.setattr("gui.widgets.model_picker.model_catalog", lambda: catalog)
    view = SettingsView(service)
    view.api_key_input.setText("unsaved-key")
    view.api_url_input.setText("https://unsaved.invalid/v1")
    view._manual_dirty = False
    before = copy.deepcopy(service.settings)
    current = view.cls_model_input.text()
    view.cls_model_input.fetch_models()
    wait_for(app, lambda: not catalog._workers)
    assert gateway.calls[0]["api_key"] == "unsaved-key"
    assert gateway.calls[0]["base_url"] == "https://unsaved.invalid/v1"
    assert service.settings == before and not view._manual_dirty
    assert not (tmp_path / "settings.json").exists()
    assert view.cls_model_input.text() == current
    assert view.tag_model_input.combo.count() == 2
    view.cls_model_input.combo.setCurrentIndex(view.cls_model_input.combo.findText("vision-b"))
    assert view._manual_dirty
    assert view.apply_settings(silent=True)
    saved = SettingsManager.load_settings(service.db)
    assert next(p for p in saved["model_providers"] if p["id"] == "main")["models"]["video_classification"] == "vision-b"
    view.prefs_controller.cancel()
    view.close()


def test_manual_recovery_selects_fetched_model_or_accepts_custom(app, service, tmp_path, monkeypatch):
    path = video(service, tmp_path)
    catalog = ModelCatalog(gateway=Gateway())
    monkeypatch.setattr("gui.widgets.model_picker.model_catalog", lambda: catalog)
    dialog = ManualRecoveryDialog(service, [path])
    dialog.mode.setCurrentIndex(dialog.mode.findData("selected"))
    dialog.provider.setCurrentIndex(dialog.provider.findData("backup"))
    dialog.model.fetch_models()
    wait_for(app, lambda: not catalog._workers)
    dialog.model.combo.setCurrentIndex(dialog.model.combo.findText("vision-b"))
    dialog.accept()
    assert dialog.options["selected_fallback"] == {"provider_id": "backup", "model": "vision-b"}
    dialog.close()
