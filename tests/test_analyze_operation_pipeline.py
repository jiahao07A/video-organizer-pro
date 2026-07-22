# -*- coding: utf-8 -*-
"""操作目标集 + 分析目标集组合（ticket 03）。"""
from core.analysis_targets import resolve_analysis_target_paths
from core.operation_targets import resolve_operation_target_paths


def test_analyze_pipeline_selected_subset():
    selected = ["a.mp4", "b.mp4"]
    visible = ["a.mp4", "b.mp4", "c.mp4"]
    ops = resolve_operation_target_paths(selected, visible)
    assert ops == selected
    status = {"a.mp4": "pending", "b.mp4": "analyzed", "c.mp4": "pending"}
    targets = resolve_analysis_target_paths(ops, status, force=False)
    assert targets == ["a.mp4"]


def test_analyze_pipeline_no_selection_uses_visible():
    visible = ["a.mp4", "b.mp4", "c.mp4"]
    ops = resolve_operation_target_paths(None, visible)
    assert ops == visible
    status = {
        "a.mp4": "pending",
        "b.mp4": "analyzed",
        "c.mp4": "failed",
    }
    targets = resolve_analysis_target_paths(ops, status, force=False)
    assert targets == ["a.mp4", "c.mp4"]


def test_analyze_pipeline_force_includes_analyzed():
    visible = ["a.mp4", "b.mp4"]
    ops = resolve_operation_target_paths([], visible)
    status = {"a.mp4": "analyzed", "b.mp4": "analyzed"}
    assert resolve_analysis_target_paths(ops, status, force=False) == []
    assert resolve_analysis_target_paths(ops, status, force=True) == ["a.mp4", "b.mp4"]