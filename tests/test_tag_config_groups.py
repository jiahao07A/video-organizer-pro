# -*- coding: utf-8 -*-
"""词表写入标签组：不因缺 version 被 v1 迁移清空。"""
from pathlib import Path
import json
import copy

from core.video_organizer_service import (
    DEFAULT_SETTINGS,
    VideoOrganizerService,
    SettingsManager,
)
from core.tag_vocab import build_vocab_draft_from_lines


def test_load_tag_config_keeps_groups_without_version(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "tag_config.json"
    # 模拟用户写入后缺 version 的文件（旧 bug 会当 v1 迁移清空）
    payload = {
        "tag_groups": [
            {"id": "mood", "name": "氛围", "tags": ["开心", "治愈"], "rules": {}},
            {"id": "subject", "name": "主体", "tags": ["男性"], "rules": {}},
        ]
    }
    cfg_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))

    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    groups = {g["id"]: g.get("tags") for g in svc.tag_config["tag_groups"]}
    assert "开心" in groups.get("mood", [])
    assert "男性" in groups.get("subject", [])
    # 磁盘应被补上 version 而不丢词
    disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert disk.get("version") == "6.0" or any(
        g.get("id") == "mood" and "开心" in (g.get("tags") or [])
        for g in disk.get("tag_groups") or []
    )


def test_commit_vocab_fills_all_groups(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "tag_config.json"
    cfg_path.write_text(json.dumps({"tag_groups": []}), encoding="utf-8")
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(cfg_path))
    vocab = tmp_path / "词.txt"
    vocab.write_text(
        "\n".join(
            [
                "# ========== 氛围 mood ==========",
                "开心 ← 快乐",
                "# ========== 主体 subject ==========",
                "男性 ← 男",
                "# ========== 场景 location ==========",
                "城市",
                "# ========== 动作 action ==========",
                "跑步",
                "# ========== 建议 custom ==========",
                "空镜",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("core.video_organizer_service.DICTIONARY_FILE", str(vocab))

    svc = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        on_log=lambda _m: None,
        db_path=str(tmp_path / "t.db"),
        results_json=str(tmp_path / "r.json"),
        results_csv=str(tmp_path / "r.csv"),
    )
    r = svc.rebuild_tag_config_from_vocab_file(str(vocab))
    assert r.get("ok") is True
    by = {g["id"]: g["tags"] for g in svc.tag_config["tag_groups"]}
    assert "开心" in by["mood"]
    assert "男性" in by["subject"]
    assert "城市" in by["location"]
    assert "跑步" in by["action"]
    assert "空镜" in by["custom"]
    # 组管理可见：至少 5 组
    assert len(svc.tag_config["tag_groups"]) >= 5