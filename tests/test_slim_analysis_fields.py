# -*- coding: utf-8 -*-
"""分析结果字段瘦身 + 去情绪（ADR-0002/0003）— 服务层接缝。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService

REMOVED_KEYS = (
    "composition",
    "rating",
    "quality_score",
    "is_proxy_needed",
    "tag_weights",
    "emotion",
)
KEPT_KEYS = ("category", "summary")


@pytest.fixture
def service(tmp_path: Path) -> VideoOrganizerService:
    return VideoOrganizerService(
        settings={**DEFAULT_SETTINGS, "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {}))},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "slim.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )


def test_build_analysis_prompts_excludes_removed_fields(service: VideoOrganizerService):
    prompts = service.ai.build_analysis_prompts()
    user = prompts["user_prompt"]
    for key in REMOVED_KEYS:
        assert key not in user, f"user_prompt 仍含已删字段 {key}"
        assert f'"{key}"' not in user
    for key in KEPT_KEYS:
        assert key in user


def test_build_analysis_prompts_json_structure_keys(service: VideoOrganizerService):
    """期望 JSON 结构段不含已删英文键。"""
    prompts = service.ai.build_analysis_prompts()
    blob = prompts["user_prompt"] + prompts["system_prompt"]
    for key in REMOVED_KEYS:
        assert f'"{key}"' not in blob, f'提示词仍含 "{key}"'
    for key in KEPT_KEYS:
        assert key in blob


def test_upsert_and_read_strips_removed_fields(service: VideoOrganizerService, tmp_path: Path):
    path = str((tmp_path / "a.mp4").resolve())
    Path(path).write_bytes(b"")
    service.db.upsert_video(
        {
            "path": path,
            "filename": "a.mp4",
            "category": "生活",
            "summary": "摘要",
            "tags": ["治愈"],
            "emotion": "平静",  # 故意传入，写入后应被硬清
            "status": "analyzed",
            "composition": "对称",
            "rating": 5,
            "quality_score": 9.9,
            "is_proxy_needed": True,
            "tag_weights": {"治愈": 0.9},
        }
    )
    rows = service.db.get_all_videos()
    assert len(rows) == 1
    row = rows[0]
    assert row.get("category") == "生活"
    assert row.get("summary") == "摘要"
    assert row.get("tags") == ["治愈"]
    # 产品层不应再暴露有效的已删字段（含 emotion）
    for key in REMOVED_KEYS:
        val = row.get(key)
        if key == "tag_weights":
            assert not val, f"tag_weights 应为空: {val}"
        elif key in ("rating", "quality_score"):
            assert not val or val == 0 or val == 0.0
        elif key == "is_proxy_needed":
            assert not val
        else:
            assert not val, f"{key} 应为空: {val}"


def test_rename_pattern_ignores_emotion_and_composition(service: VideoOrganizerService, tmp_path: Path):
    """重命名替换不再依赖 emotion/composition；占位符残留应变成空串。"""
    from core.video_organizer_service import FileManager

    item = {
        "category": "A",
        "tags": ["t1"],
        "summary": "s",
        "emotion": "SHOULD_NOT_APPEAR_E",
        "composition": "SHOULD_NOT_APPEAR_C",
        "filename": "orig.mp4",
    }
    pattern = "{category}-{emotion}-{composition}-{original_name}"
    tags_str = "_".join(item["tags"])
    summary_safe = FileManager.sanitize_filename(item.get("summary") or "", max_len=50)
    new_fn = (
        pattern.replace("{category}", str(item["category"]))
        .replace("{tags}", tags_str)
        .replace("{summary}", str(summary_safe))
        .replace("{emotion}", "")
        .replace("{composition}", "")
        .replace("{original_name}", "orig.mp4")
    )
    assert "SHOULD_NOT_APPEAR" not in new_fn
    assert new_fn == "A---orig.mp4"


def test_export_ale_has_no_emotion_column(service: VideoOrganizerService, tmp_path: Path):
    path = str((tmp_path / "b.mp4").resolve())
    Path(path).write_bytes(b"")
    service.db.upsert_video(
        {
            "path": path,
            "filename": "b.mp4",
            "category": "生活",
            "summary": "摘要行",
            "tags": ["氛围词"],
            "status": "analyzed",
        }
    )
    out = tmp_path / "out.ale"
    assert service.export_to_ale(str(out)) is True
    text = out.read_text(encoding="utf-8-sig")
    assert "Emotion" not in text
    assert "Name\tDescription\tKeywords\tCategory\tSummary" in text