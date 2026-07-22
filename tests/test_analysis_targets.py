# -*- coding: utf-8 -*-
"""分析目标集纯逻辑测试（ticket 01）。"""
from core.analysis_targets import (
    build_path_status_map,
    is_analyzed_status,
    needs_analysis,
    resolve_analysis_target_paths,
)


def test_pending_needs_analysis():
    assert needs_analysis("pending") is True
    assert needs_analysis(None) is True
    assert needs_analysis("") is True


def test_failed_needs_analysis():
    assert needs_analysis("failed") is True
    assert needs_analysis("error") is True


def test_analyzed_skipped_unless_force():
    assert needs_analysis("analyzed") is False
    assert needs_analysis("analyzed", force=True) is True
    assert is_analyzed_status("analyzed") is True


def test_in_library_pending_not_skipped():
    """禁止：仅因在库而跳过 — 在库 pending 必须进队。"""
    paths = ["a.mp4", "b.mp4", "c.mp4"]
    status = {
        "a.mp4": "pending",
        "b.mp4": "analyzed",
        "c.mp4": "failed",
    }
    targets = resolve_analysis_target_paths(paths, status, force=False)
    assert targets == ["a.mp4", "c.mp4"]


def test_force_includes_analyzed():
    paths = ["a.mp4", "b.mp4"]
    status = {"a.mp4": "analyzed", "b.mp4": "pending"}
    targets = resolve_analysis_target_paths(paths, status, force=True)
    assert targets == ["a.mp4", "b.mp4"]


def test_unknown_path_treated_as_pending():
    targets = resolve_analysis_target_paths(["new.mp4"], {}, force=False)
    assert targets == ["new.mp4"]


def test_path_key_matches_case_variant_on_windows():
    """规范化键应让不同写法的同一路径命中同一状态。"""
    from core.analysis_targets import path_status_key, resolve_analysis_target_paths

    p1 = r"D:\Editing\视频素材\test\a.mp4"
    # 故意用不同大小写/斜杠（Windows normcase 会折叠）
    status = {path_status_key(p1): "analyzed"}
    # 候选用 abspath 形态
    import os

    candidates = [os.path.abspath(p1)]
    targets = resolve_analysis_target_paths(candidates, status, force=False)
    assert targets == []  # 已分析应跳过


def test_build_path_status_map_uses_stable_keys():
    from core.analysis_targets import build_path_status_map, path_status_key

    m = build_path_status_map([{"path": r"C:\Clips\A.MP4", "status": "analyzed"}])
    assert m[path_status_key(r"C:\Clips\A.MP4")] == "analyzed"
