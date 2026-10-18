# -*- coding: utf-8 -*-
"""首次升级 GUI 门禁回归测试。"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import core.video_organizer_service as service_module
from core.startup_upgrade import UpgradeStatus
from core.video_organizer_service import VideoOrganizerService
from gui.startup_upgrade_dialog import StartupUpgradeDialog


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _wait_for(app, predicate, timeout=5000):
    deadline = QTest.qWait
    elapsed = 0
    while elapsed < timeout:
        app.processEvents()
        if predicate():
            return
        deadline(20)
        elapsed += 20
    assert predicate(), "GUI 状态未在时限内满足"


def test_startup_upgrade_dialog_confirms_and_reports_progress(
    qt_app, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        service_module, "get_settings_file_path", lambda: str(tmp_path / "settings.json")
    )
    monkeypatch.setattr(service_module, "BACKUP_DIR", str(tmp_path / "backups"))
    source = tmp_path / "video_analysis_results.json"
    source.write_text(
        json.dumps(
            [
                {
                    "path": str(tmp_path / f"clip-{index}.mp4"),
                    "filename": f"clip-{index}.mp4",
                    "status": "analyzed",
                    "summary": str(index),
                    "tags": [],
                    "tag_groups": {},
                }
                for index in range(3)
            ]
        ),
        encoding="utf-8",
    )
    pending = VideoOrganizerService.inspect_startup_upgrade(
        db_path=str(tmp_path / "library.db"),
        results_json=str(source),
        include_import_report=False,
    )
    assert pending.status is UpgradeStatus.NEEDS_CONFIRMATION

    dialog = StartupUpgradeDialog(pending)
    dialog.show()
    _wait_for(qt_app, lambda: dialog.confirm_button.isEnabled())
    assert "可导入 3 条" in dialog.summary_label.text()

    dialog.confirm_button.click()
    _wait_for(qt_app, lambda: dialog.result() != 0)

    assert dialog.final_result is not None
    assert dialog.final_result.status is UpgradeStatus.COMPLETED
    assert dialog.final_result.report["imported_rows"] == 3
    assert not any(
        thread.isRunning() for thread in dialog.findChildren(QThread)
    )
    dialog.deleteLater()
    qt_app.processEvents()
