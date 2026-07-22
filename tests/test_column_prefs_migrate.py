# -*- coding: utf-8 -*-
"""列偏好迁移（去选择列）。"""
from gui.models.video_table import migrate_table_column_prefs


def test_migrate_legacy_8_col_shifts():
    legacy = {"0": False, "1": True, "2": True, "3": False, "7": True}
    out = migrate_table_column_prefs(legacy, 7)
    assert "0" not in out or out.get("0") is True  # 旧 1 → 新 0
    assert out.get("0") is True
    assert out.get("2") is False  # 旧 3 → 新 2
    assert "7" not in out


def test_migrate_schema_v2_no_shift():
    prefs = {"0": False, "1": True, "_schema": "no_check_col"}
    out = migrate_table_column_prefs(prefs, 7)
    assert out.get("0") is False
    assert out.get("1") is True
    assert "_schema" not in out


def test_migrate_empty():
    assert migrate_table_column_prefs({}) == {}
    assert migrate_table_column_prefs(None) == {}