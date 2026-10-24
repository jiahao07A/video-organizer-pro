# -*- coding: utf-8 -*-
"""标签导入服务格式解析与 tag_groups 兼容测试。"""
from core.tag_import_service import TagImportService, _tag_groups_from_config


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


def test_read_tag_file_parses_numbered_sections_and_aliases(tmp_path):
    class FakeAI:
        tag_config = {}

    source = tmp_path / "词_优化版.txt"
    source.write_text(
        "说明文本不应进入标签列表。\n"
        "一、主体：画面里有什么\n"
        "# 人物与人群\n"
        "人物\n"
        "男性 ← 男人, 男\n"
        "二、动作：主体正在做什么\n"
        "读书 <- 看书\n"
        "六、画面风格：画面如何呈现\n"
        "纪实\n",
        encoding="utf-8",
    )

    importer = TagImportService(FakeAI())
    tags = importer.read_tags_from_file(str(source))

    assert tags == ["人物", "男性", "读书", "纪实"]
    assert importer.last_import_aliases == {
        "男人": "男性",
        "男": "男性",
        "看书": "读书",
    }
    assert importer.last_import_groups == {
        "人物": "subject",
        "男性": "subject",
        "读书": "action",
        "纪实": "custom",
    }


def test_read_tag_file_parses_vocab_headers_and_preserves_order(tmp_path):
    class FakeAI:
        tag_config = {}

    source = tmp_path / "词.txt"
    source.write_text(
        "# 说明\n"
        "# ========== 氛围 mood ==========\n"
        "开心 ← 快乐\n"
        "幸福\n"
        "# ========== 主体 subject ==========\n"
        "男性 ← 男人\n",
        encoding="utf-8",
    )

    importer = TagImportService(FakeAI())

    assert importer.read_tags_from_file(str(source)) == ["开心", "幸福", "男性"]
    assert importer.last_import_aliases == {"快乐": "开心", "男人": "男性"}
    assert importer.last_import_groups == {
        "开心": "mood",
        "幸福": "mood",
        "男性": "subject",
    }
