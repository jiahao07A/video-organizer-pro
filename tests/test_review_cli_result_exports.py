"""RC11 命令行范围和工作台显式导出；所有素材、配置与数据库均隔离。"""
import csv
import json
from pathlib import Path

import pytest

import video_organizer as cli
from core.video_organizer_service import SettingsManager, VideoOrganizerService


@pytest.fixture
def service(tmp_path):
    return VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), db_path=str(tmp_path / "library.db"),
                                 results_json=str(tmp_path / "default.json"), on_log=lambda _: None)


def register(service, path, text):
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(text.encode())
    service.media_registry.register(str(path))
    row = service.db.get_video_by_path(str(path))
    row.update(category="A\t类别", summary="首行\r\n次行", tags=["a\tb", "中文"], status="analyzed")
    service.db.upsert_video(row)
    return str(path)


def test_cli_empty_directory_never_renames_library(service, tmp_path, monkeypatch):
    outside = Path(register(service, tmp_path / "outside.mp4", "outside"))
    target = tmp_path / "empty"
    target.mkdir()
    monkeypatch.setattr(cli, "VideoOrganizerService", lambda: service)
    assert cli.main([str(target), "--rename"]) != 0
    assert outside.read_bytes() == b"outside"
    assert service.db.get_video_by_path(str(outside)) is not None


def test_cli_real_preview_and_rename_stay_in_requested_directory(service, tmp_path, monkeypatch, capsys):
    source = Path(register(service, tmp_path / "inside" / "wanted.mp4", "first"))
    record = service.db.get_video_by_path(str(source))
    record["category"] += "\x01\x00"
    service.db.upsert_video(record)
    outside = Path(register(service, tmp_path / "other.mp4", "second"))
    monkeypatch.setattr(cli, "VideoOrganizerService", lambda: service)
    assert cli.main([str(source), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "wanted.mp4" in output and "→" in output and "other.mp4" not in output
    assert source.exists()
    assert cli.main([str(source.parent), "--rename"]) == 0
    assert not source.exists() and outside.read_bytes() == b"second"
    renamed = next(source.parent.glob("*.mp4"))
    assert renamed.read_bytes() == b"first"
    assert all(ord(char) >= 32 for char in renamed.name)
    assert renamed.suffix == ".mp4"


def test_cli_processing_workers_and_analysis_failure_gate(service, tmp_path, monkeypatch):
    path = register(service, tmp_path / "clip.mp4", "sample")
    calls = []
    def fail(paths, *, execution_options):
        calls.append((paths, execution_options))
        return {"failed": 1, "message": "分析失败"}
    monkeypatch.setattr(cli, "VideoOrganizerService", lambda: service)
    monkeypatch.setattr(service, "run_analysis", fail)
    assert cli.main([path, "--workers", "3", "--rename"]) != 0
    assert calls == [([path], {"max_workers": 3})]
    assert Path(path).exists()


def test_cli_initialization_failure_is_a_nonzero_exit(tmp_path, monkeypatch, capsys):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"clip")
    def failed_service():
        raise OSError("storage unavailable")
    monkeypatch.setattr(cli, "VideoOrganizerService", failed_service)
    assert cli.main([str(path)]) == 1
    assert "storage unavailable" in capsys.readouterr().err


def test_installer_uninstalls_from_file_manifest_only():
    source = (Path(__file__).resolve().parents[1] / "installer" / "VideoOrganizer.iss").read_text(encoding="utf-8")
    rules = source.split("[UninstallDelete]", 1)[1]
    directives = [line for line in rules.splitlines() if line.strip() and not line.lstrip().startswith(";")]
    assert directives == ['Type: files; Name: "{app}\\.video-organizer-install.json"']


def test_ale_uses_configured_columns_and_one_physical_row(service, tmp_path):
    path = register(service, tmp_path / "one.mp4", "one")
    service.tag_config["global_settings"]["export_schemes"]["ale_columns"] = ["Category", "Summary", "Keywords", "Name"]
    out = tmp_path / "out.ale"
    assert service.export_to_ale(str(out), [path])
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[lines.index("Column")+1].split("\t") == ["Category", "Summary", "Keywords", "Name"]
    assert lines[lines.index("Data")+1:] == ["A 类别\t首行 次行\ta b, 中文\tone.mp4"]


def test_ale_defaults_clean_every_field(service, tmp_path):
    path = register(service, tmp_path / "one.mp4", "one")
    row = service.db.get_video_by_path(path)
    row["filename"] = "one\r\nline\t.mp4"
    service.db.upsert_video(row)
    service.tag_config["global_settings"]["export_schemes"].pop("ale_columns", None)
    out = tmp_path / "out.ale"
    assert service.export_to_ale(str(out), [path])
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    rows = lines[lines.index("Data")+1:]
    assert rows == ["one line .mp4\t首行 次行\ta b, 中文\tA 类别\t首行 次行"]


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_result_export_bounds_real_rows_and_preserves_fields(service, tmp_path, fmt):
    one = register(service, tmp_path / "one.mp4", "one")
    register(service, tmp_path / "outside.mp4", "outside")
    dup = tmp_path / "duplicate.mp4"
    dup.write_bytes(b"one")
    assert service.media_registry.register(str(dup))["duplicate_of"]
    out = tmp_path / ("results." + fmt)
    assert service.export_analysis_results(str(out), [one, str(dup)]) == 1
    if fmt == "json":
        rows = json.loads(out.read_text(encoding="utf-8"))
        assert [(v["path"], v["tags"], v["summary"]) for v in rows] == [(one, ["a\tb", "中文"], "首行\r\n次行")]
    else:
        with out.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert [(v["文件路径"], v["标签1"], v["标签2"], v["摘要"]) for v in rows] == [(one, "a\tb", "中文", "首行\r\n次行")]
    previous = out.read_bytes()
    assert service.export_analysis_results(str(out), []) == 0
    assert out.read_bytes() == previous
    assert not (tmp_path / "default.json").exists()
    with pytest.raises(ValueError):
        service.export_analysis_results(one, [one])
    assert Path(one).read_bytes() == b"one"
    with pytest.raises(ValueError):
        service.export_analysis_results(service._settings_path, [one])


def test_workstation_menu_exports_only_the_visible_filter(service, tmp_path, qt_app, monkeypatch):
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from gui.views.workstation import WorkstationView

    keep = register(service, tmp_path / "keep.mp4", "keep")
    other = register(service, tmp_path / "other.mp4", "other")
    service.replace_work_scope([keep, other], scan=False)
    view = WorkstationView(service)
    out = tmp_path / "selection.json"
    notices = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(out), "JSON (*.json)"))
    monkeypatch.setattr(QMessageBox, "information", lambda _, title, msg: notices.append(title))
    monkeypatch.setattr(QMessageBox, "warning", lambda _, title, msg: notices.append(msg))
    try:
        view.show()
        view.search_input.setText("keep.mp4")
        timer = QElapsedTimer()
        timer.start()
        while timer.elapsed() < 5000 and not (view.model.rowCount() == 1 and view.model.videos[0]["path"] == keep):
            QTest.qWait(20)
        assert [row["path"] for row in view.model.videos] == [keep]
        next(action for action in view.export_results_btn.menu().actions() if action.text() == "JSON").trigger()
        timer.restart()
        while timer.elapsed() < 5000 and not notices:
            QTest.qWait(20)
        assert notices == ["导出完成"]
        assert [row["path"] for row in json.loads(out.read_text(encoding="utf-8"))] == [keep]
    finally:
        view.close()
        view.deleteLater()


def test_result_export_protects_internal_marker_paths_but_not_unrelated_names(service, tmp_path, monkeypatch):
    from core.data_directory import DataDirectoryManager
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    manager = DataDirectoryManager(application_directory=str(tmp_path / "installation"))
    monkeypatch.setattr("core.video_organizer_service._service_data_manager", lambda: manager)
    selected = [register(service, tmp_path / "clip.mp4", "clip")]
    for protected in (Path(manager.locator_path), Path(manager.fallback_locator_path),
                      Path(manager.application_directory) / ".video-organizer-install.json"):
        with pytest.raises(ValueError):
            service.export_analysis_results(str(protected), selected)
    ordinary = tmp_path / ".video-organizer-install.json"
    assert service.export_analysis_results(str(ordinary), selected) == 1


def test_fcpx_all_duplicates_does_not_claim_success(service, tmp_path, qt_app, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from gui.views.workstation import WorkstationView
    register(service, tmp_path / "original.mp4", "same")
    dup = tmp_path / "copy.mp4"
    dup.write_bytes(b"same")
    assert service.media_registry.register(str(dup))["duplicate_of"]
    service.replace_work_scope([str(dup)], scan=False)
    view = WorkstationView(service)
    out = tmp_path / "untouched.fcpxml"
    out.write_bytes(b"sentinel")
    notices = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(out), ""))
    for meth in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, meth, lambda _, title, msg: notices.append((title, msg)))
    try:
        view.show()
        view.export_fcpx_btn.click()
        for _ in range(150):
            QTest.qWait(20)
            if notices:
                break
        assert len(notices) == 1 and "没有可导出" in notices[0][1]
        assert out.read_bytes() == b"sentinel"
    finally:
        view.close()
        view.deleteLater()
