# -*- coding: utf-8 -*-
"""标签导入服务兼容 tag_groups（运行时 bug 修复）。"""
from core.tag_import_service import _tag_groups_from_config


def test_tag_groups_from_v6_config():
    cfg = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "rules": {"local_prompt": "基调"}, "tags": []},
            {"id": "custom", "name": "建议", "tags": []},
        ]
    }
    groups = _tag_groups_from_config(cfg)
    assert len(groups) == 2
    assert groups[0]["id"] == "mood"


def test_tag_groups_from_legacy_config_key():
    cfg = {"config": [{"id": "mood", "display_name": "氛围", "tags": []}]}
    groups = _tag_groups_from_config(cfg)
    assert groups[0]["id"] == "mood"


def test_empty_config():
    assert _tag_groups_from_config(None) == []
    assert _tag_groups_from_config({}) == []