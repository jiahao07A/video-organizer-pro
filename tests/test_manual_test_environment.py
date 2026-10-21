from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.manual_test_environment import (
    DIAGNOSTICS_ENV,
    SESSION_ENV,
    ManualTestEnvironment,
)


def test_manual_environment_isolated_and_cleans_data_but_keeps_diagnostics(tmp_path: Path):
    diagnostics_root = tmp_path / "diagnostics"
    old_log_dir = os.environ.get(DIAGNOSTICS_ENV)
    old_session = os.environ.get(SESSION_ENV)

    with ManualTestEnvironment(
        record_count=3,
        diagnostics_root=str(diagnostics_root),
        create_media_files=True,
    ) as environment:
        assert environment.service is not None
        assert environment.temp_root is not None
        assert environment.data_root is not None
        assert environment.data_root.parent == environment.temp_root
        assert Path(environment.service.db.db_path) == Path(environment.paths.database)
        assert Path(environment.paths.database).exists()
        assert Path(environment.paths.settings).exists()
        assert Path(environment.paths.tag_config).exists()
        assert len(environment.service.db.get_all_videos()) == 3
        assert Path(environment.paths.database).resolve() != Path("video_organizer.db").resolve()
        assert os.environ[DIAGNOSTICS_ENV] == str(environment.diagnostics_path)
        assert os.environ[SESSION_ENV] == environment.session_id
        session_id = environment.session_id
        temp_root = environment.temp_root

    report = environment.cleanup_report
    assert report is not None
    assert report["temp_data_removed"] is True
    assert not temp_root.exists()
    session_dir = diagnostics_root / session_id
    assert session_dir.exists()
    assert (session_dir / "application.log").exists()
    assert (session_dir / "cleanup-report.json").exists()
    cleanup = json.loads((session_dir / "cleanup-report.json").read_text(encoding="utf-8"))
    assert cleanup["temp_data_removed"] is True

    assert os.environ.get(DIAGNOSTICS_ENV) == old_log_dir
    assert os.environ.get(SESSION_ENV) == old_session


def test_manual_environment_cleanup_is_idempotent_and_restores_log_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv(DIAGNOSTICS_ENV, raising=False)
    monkeypatch.delenv(SESSION_ENV, raising=False)
    environment = ManualTestEnvironment(
        record_count=0,
        diagnostics_root=str(tmp_path / "diagnostics"),
        create_media_files=False,
    ).start()
    first = environment.close()
    second = environment.close()

    assert first == second
    assert first["temp_data_removed"] is True
    assert (environment.diagnostics_path / "cleanup-report.json").exists()
    assert os.environ.get(DIAGNOSTICS_ENV) is None
    assert os.environ.get(SESSION_ENV) is None
