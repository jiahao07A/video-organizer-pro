"""离屏Qt验证继承、有效值、排序及三种手动模式。"""
import os
import copy
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QScrollArea
from core.analysis_recovery import RecoveryStore, DEFAULT_POLICY
from gui.widgets.recovery_controls import RecoveryPolicyEditor, ManualRecoveryDialog, RecoverySettingsDialog, VideoRecoverySettingsDialog
from gui.widgets.recovery_controls import RecoverySettingsPanel
from gui.views.settings import SettingsView
from core.video_organizer_service import SettingsManager
from gui.widgets.filter_panel import FilterPanel
from gui.models.video_table import VideoTableModel, COL_STATUS
from gui.models.settings_catalog import search_entries
from tests.test_http400_recovery import service, video, fallback


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_editor_inheritance_false_and_empty_list(app, service):
    editor = RecoveryPolicyEditor(service.settings, base={**DEFAULT_POLICY, "fallbacks": [fallback()]})
    assert editor.override() == {}
    editor.inherit["enabled"].setChecked(False)
    editor.fields["enabled"].setChecked(False)
    editor.inherit["fallbacks"].setChecked(False)
    editor.fields["fallbacks"].selectRow(0)
    editor.remove_row("fallbacks")
    assert editor.override() == {"enabled": False, "fallbacks": []}
    editor.inherit["enabled"].setChecked(True)
    assert editor.effective()["enabled"]


def test_unknown_exclusion_task_is_preserved(app, service):
    rule = {"task_key": "future_task", "keyword": "invalid"}
    editor = RecoveryPolicyEditor(service.settings)
    editor.inherit["exclusions"].setChecked(False)
    editor.add_row("exclusions", rule)
    assert editor.override()["exclusions"] == [rule]


def test_editor_order_and_parent_updates(app, service):
    dialog = RecoverySettingsDialog(service)
    editor = dialog.global_editor
    editor.inherit["fallbacks"].setChecked(False)
    editor.add_row("fallbacks", fallback())
    editor.add_row("fallbacks", fallback("last"))
    editor.fields["fallbacks"].selectRow(1)
    editor.move_row("fallbacks", -1)
    assert editor.override()["fallbacks"][0]["provider_id"] == "last"
    assert dialog.task_editor.effective()["fallbacks"][0]["provider_id"] == "last"
    assert "last" in editor.preview.text()
    dialog.close()


@pytest.mark.parametrize("mode", ["policy", "primary", "selected"])
def test_manual_options_and_video_override(app, service, tmp_path, mode):
    path = video(service, tmp_path)
    dialog = ManualRecoveryDialog(service, [path])
    dialog.mode.setCurrentIndex(dialog.mode.findData(mode))
    if mode == "selected":
        dialog.provider.setCurrentIndex(dialog.provider.findData("backup"))
        dialog.model.setText("vision-backup")
    dialog.editor.inherit["request_budget"].setChecked(False)
    dialog.editor.fields["request_budget"].setValue(3)
    dialog.save_video.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.options["recovery_mode"] == mode
    assert dialog.options["recovery_override"] == {"request_budget": 3}
    assert RecoveryStore(service.db).override(path) == {"request_budget": 3}
    if mode == "selected":
        assert dialog.options["selected_fallback"]["provider_id"] == "backup"


def test_filter_emits_and_resets(app, service):
    panel = FilterPanel(service.settings)
    values = []
    panel.filterChanged.connect(values.append)
    panel.recovery_combo.setCurrentIndex(1)
    assert values[-1]["recovery_filter"] == "unresolved"
    panel.reset_filter()
    assert values[-1]["recovery_filter"] == ""
    assert any(x.entry.widget_key == "http400_recovery" for x in search_entries("兜底"))


def test_status_keeps_previous_success_visible(app):
    model = VideoTableModel()
    model.update_data([{"path": "a.mp4", "filename": "a.mp4", "status": "analyzed", "recovery_state": "exhausted"}])
    assert "重新分析失败·400" in model.data(model.index(0, COL_STATUS))


def test_video_policy_can_be_saved_without_starting_requests(app, service, tmp_path):
    path = video(service, tmp_path)
    dialog = VideoRecoverySettingsDialog(service, path)
    dialog.editor.inherit["enabled"].setChecked(False)
    dialog.editor.fields["enabled"].setChecked(False)
    dialog.accept()
    assert RecoveryStore(service.db).override(path) == {"enabled": False}
    assert not service.db.execute_query("SELECT * FROM video_recovery_rounds")


def test_global_form_is_editable_and_advanced_values_survive_scope_switch(app, service):
    panel = RecoverySettingsPanel(service)
    global_editor = panel.global_editor
    assert all(widget.isEnabled() for widget in global_editor.fields.values())
    assert all(widget.isHidden() for widget in global_editor.inherit.values())
    assert global_editor.advanced_body.isHidden()
    assert global_editor.fields["jitter"].value() == 0  # fixture覆盖的值
    global_editor.advanced_toggle.setChecked(True)
    global_editor.fields["jitter"].setValue(35)
    global_editor.fields["request_budget"].setValue(6)
    panel.scope.setCurrentIndex(1)
    assert panel.task_editor.fields["request_budget"].value() == 6
    panel.task_editor.inherit["primary_extra_attempts"].setChecked(False)
    panel.task_editor.fields["primary_extra_attempts"].setValue(0)
    panel.scope.setCurrentIndex(2)
    panel.scope.setCurrentIndex(0)
    global_editor.advanced_toggle.setChecked(False)
    policy = panel.policy()
    assert policy["global"]["jitter"] == 0.35
    assert policy["tasks"]["video_classification"] == {"primary_extra_attempts": 0}
    assert policy["tasks"]["content_description"] == {}


def test_main_settings_save_recovery_together_and_reload(app, service, monkeypatch):
    view = SettingsView(service)
    panel = view.http400_settings
    panel.scope.setCurrentIndex(1)
    panel.scope.setCurrentIndex(0)
    panel.global_editor.details_toggle.setChecked(True)
    panel.global_editor.advanced_toggle.setChecked(True)
    assert not view._manual_dirty  # 仅浏览设置不提示保存。
    before = copy.deepcopy(service.settings["processing"]["http400_recovery"])
    panel.global_editor.fields["enabled"].setChecked(False)
    panel.global_editor.fields["jitter"].setValue(25)
    panel.task_editor.inherit["fallbacks"].setChecked(False)
    panel.task_editor.inherit["primary_extra_attempts"].setChecked(False)
    panel.task_editor.fields["primary_extra_attempts"].setValue(0)
    assert service.settings["processing"]["http400_recovery"] == before
    assert view._manual_dirty
    assert view.focus_setting("http400_recovery")
    assert view.tabs.currentIndex() == 0
    assert view.apply_settings(silent=True)
    saved = SettingsManager.load_settings(service.db)["processing"]["http400_recovery"]
    assert saved["global"]["enabled"] is False
    assert saved["global"]["jitter"] == 0.25
    assert saved["tasks"]["video_classification"] == {"fallbacks": [], "primary_extra_attempts": 0}
    reloaded = RecoverySettingsPanel(service)
    assert reloaded.task_editor.override() == saved["tasks"]["video_classification"]
    assert reloaded.global_editor.fields["jitter"].value() == 25
    view.prefs_controller.cancel()
    view.close()


def test_main_settings_invalid_recovery_does_not_save(app, service, monkeypatch):
    view = SettingsView(service)
    before = copy.deepcopy(service.settings["processing"]["http400_recovery"])
    view.http400_settings.global_editor.add_row("fallbacks", {"provider_id": "backup", "model": ""})
    saved = []
    monkeypatch.setattr(SettingsManager, "save_settings", lambda *a, **kw: saved.append(True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.Ok)
    assert view.apply_settings(silent=True) is False
    assert not saved
    assert service.settings["processing"]["http400_recovery"] == before
    view.prefs_controller.cancel()
    view.close()


def test_main_settings_failed_save_restores_previous_recovery(app, service, monkeypatch):
    view = SettingsView(service)
    before = copy.deepcopy(service.settings["processing"]["http400_recovery"])
    view.http400_settings.global_editor.fields["request_budget"].setValue(4)
    def fail(*args, **kwargs):
        raise OSError("模拟磁盘写入失败")
    monkeypatch.setattr(SettingsManager, "save_settings", fail)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: QMessageBox.Ok)
    assert view.apply_settings(silent=True) is False
    assert service.settings["processing"]["http400_recovery"] == before
    view.prefs_controller.cancel()
    view.close()


def test_expanded_settings_tables_do_not_overlap_buttons(app, service):
    from gui.styles import get_main_style
    panel = RecoverySettingsPanel(service)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(panel)
    scroll.setStyleSheet(get_main_style(theme="light"))
    scroll.resize(900, 600)
    scroll.show()
    panel.scope.setCurrentIndex(1)
    editor = panel.task_editor
    editor.advanced_toggle.setChecked(True)
    for _ in range(3):
        app.processEvents()
    for key in ("fallbacks", "exclusions"):
        table = editor.fields[key]
        add = next(button for button in table.parentWidget().findChildren(QPushButton) if button.text() == "添加")
        button_top = add.mapTo(table.parentWidget(), add.rect().topLeft()).y()
        assert button_top > table.geometry().bottom()
    expanded_height = panel.sizeHint().height()
    editor.advanced_toggle.setChecked(False)
    for _ in range(3):
        app.processEvents()
    assert panel.sizeHint().height() < expanded_height
    scroll.close()
