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
