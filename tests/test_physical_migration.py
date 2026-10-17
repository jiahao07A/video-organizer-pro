from pathlib import Path

from core.physical_migration import build_physical_migration_plan


def test_build_plan_is_read_only_and_keeps_requested_and_actual_target(tmp_path: Path):
    source = str((tmp_path / "source" / "clip.mp4").resolve())
    requested = str((tmp_path / "organized" / "Broll" / "clip.mp4").resolve())
    existing = {source, requested}
    row = {"path": source, "category": "Broll"}

    plan = build_physical_migration_plan(
        [row],
        str(tmp_path / "organized"),
        path_exists=lambda path: path in existing,
        path_writable=lambda _path: True,
    )

    assert row == {"path": source, "category": "Broll"}
    assert plan[0]["source_path"] == source
    assert plan[0]["requested_target_path"] == requested
    assert plan[0]["target_path"].endswith("clip_1.mp4")
    assert plan[0]["target_conflict"] is True
    assert plan[0]["status"] == "review"


def test_build_plan_marks_missing_source_and_unwritable_target(tmp_path: Path):
    source = str((tmp_path / "missing.mp4").resolve())
    plan = build_physical_migration_plan(
        [{"path": source, "category": "Other"}],
        str(tmp_path / "organized"),
        path_exists=lambda _path: False,
        path_writable=lambda _path: False,
    )

    assert plan[0]["status"] == "blocked"
    assert "源文件不存在" in plan[0]["risk_flags"]
    assert "目标目录不可写" in plan[0]["risk_flags"]
