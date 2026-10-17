import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.views.workstation import PhysicalMigrationPreviewDialog


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _migration_rows():
    return [
        {
            "source_path": "C:/clips/b.mp4",
            "requested_target_path": "D:/organized/B/b.mp4",
            "target_path": "D:/organized/B/b_1.mp4",
            "category": "B",
            "status": "review",
            "risk_flags": ["目标已存在，已生成不覆盖路径"],
            "target_conflict": True,
        },
        {
            "source_path": "C:/clips/a.mp4",
            "requested_target_path": "D:/organized/A/a.mp4",
            "target_path": "D:/organized/A/a.mp4",
            "category": "A",
            "status": "ready",
            "risk_flags": [],
            "target_conflict": False,
        },
    ]


def test_preview_shows_target_scope_and_filtered_summary(qt_app):
    dialog = PhysicalMigrationPreviewDialog(
        _migration_rows(),
        "D:/organized",
        target_paths=["C:/clips/a.mp4", "C:/clips/b.mp4"],
        work_scope_paths=["C:/clips"],
        target_summary="操作目标集：列表选中 2 条 · 实际处理 2 条",
        filter_summary="分类=B-Roll",
    )

    assert "操作目标集：列表选中 2 条" in dialog.target_summary_label.text()
    assert "工作范围素材 1 条" in dialog.summary_label.text()
    assert "操作目标集 2 条" in dialog.summary_label.text()
    assert "筛选状态：分类=B-Roll" in dialog.summary_label.text()
    rows_by_source = {
        dialog.table.item(row, 2).text(): row
        for row in range(dialog.table.rowCount())
    }
    row_b = rows_by_source["C:/clips/b.mp4"]
    assert dialog.table.item(row_b, 3).text() == "D:/organized/B/b.mp4"
    assert dialog.table.item(row_b, 4).text() == "D:/organized/B/b_1.mp4"
    assert set(dialog.execution_paths()) == {"C:/clips/a.mp4", "C:/clips/b.mp4"}
    dialog.close()


def test_preview_exclusion_and_operation_exclusion_share_execution_snapshot(qt_app):
    dialog = PhysicalMigrationPreviewDialog(
        _migration_rows(),
        "D:/organized",
        target_paths=["C:/clips/a.mp4", "C:/clips/b.mp4"],
        work_scope_paths=["C:/clips"],
    )
    row_b = next(
        row for row in range(dialog.table.rowCount())
        if dialog.table.item(row, 2).text() == "C:/clips/b.mp4"
    )
    checkbox = dialog.table.item(row_b, 0)
    checkbox.setCheckState(Qt.Unchecked)

    assert dialog.execution_paths() == ["C:/clips/a.mp4"]
    assert "当前选择 1 条" in dialog.summary_label.text()
    assert "已排除 1 条" in dialog.summary_label.text()

    dialog.operation_checkbox.setChecked(False)
    assert dialog.execution_paths() == []
    assert all(
        item.exclusion_reason == "用户排除操作类型"
        for item in dialog.confirmed_plan().excluded_items
    )
    assert not dialog.confirm_button.isEnabled()
    dialog.close()


def test_preview_sort_keeps_checkbox_and_source_path_together(qt_app):
    dialog = PhysicalMigrationPreviewDialog(
        _migration_rows(),
        "D:/organized",
        target_paths=["C:/clips/a.mp4", "C:/clips/b.mp4"],
    )
    dialog.table.sortItems(2, Qt.AscendingOrder)
    row_b = next(
        row for row in range(dialog.table.rowCount())
        if dialog.table.item(row, 2).text() == "C:/clips/b.mp4"
    )
    assert dialog.table.item(row_b, 3).text() == "D:/organized/B/b.mp4"
    assert dialog.table.item(row_b, 4).text() == "D:/organized/B/b_1.mp4"
    dialog.table.item(row_b, 0).setCheckState(Qt.Unchecked)

    assert dialog.execution_paths() == ["C:/clips/a.mp4"]
    assert "当前选择 1 条" in dialog.summary_label.text()
    dialog.close()
