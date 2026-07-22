# -*- coding: utf-8 -*-
"""操作目标集纯逻辑测试（ticket 01）。"""
import os

from core.analysis_targets import path_status_key
from core.operation_targets import resolve_operation_target_paths


def test_nonempty_selected_uses_selected_order():
    selected = ["b.mp4", "a.mp4", "c.mp4"]
    visible = ["a.mp4", "b.mp4", "c.mp4", "d.mp4"]
    assert resolve_operation_target_paths(selected, visible) == selected


def test_empty_selected_uses_visible():
    visible = ["a.mp4", "b.mp4"]
    assert resolve_operation_target_paths([], visible) == visible
    assert resolve_operation_target_paths(None, visible) == visible


def test_empty_visible_and_empty_selected_is_empty():
    assert resolve_operation_target_paths([], []) == []
    assert resolve_operation_target_paths(None, []) == []
    assert resolve_operation_target_paths(None, None) == []  # type: ignore[arg-type]


def test_selected_dedupes_preserving_first_order():
    selected = ["a.mp4", "b.mp4", "a.mp4", "c.mp4"]
    assert resolve_operation_target_paths(selected, ["x.mp4"]) == [
        "a.mp4",
        "b.mp4",
        "c.mp4",
    ]


def test_scope_filters_selected_to_subset():
    selected = ["a.mp4", "out.mp4", "b.mp4"]
    visible = ["a.mp4", "b.mp4", "out.mp4", "c.mp4"]
    scope = ["a.mp4", "b.mp4", "c.mp4"]
    assert resolve_operation_target_paths(
        selected, visible, scope_paths=scope
    ) == ["a.mp4", "b.mp4"]


def test_scope_filters_visible_when_no_selection():
    visible = ["a.mp4", "out.mp4", "b.mp4"]
    scope = ["a.mp4", "b.mp4"]
    assert resolve_operation_target_paths(
        None, visible, scope_paths=scope
    ) == ["a.mp4", "b.mp4"]


def test_scope_none_means_no_constraint():
    selected = ["a.mp4", "out.mp4"]
    assert resolve_operation_target_paths(
        selected, ["x.mp4"], scope_paths=None
    ) == selected


def test_scope_empty_list_yields_empty():
    """scope_paths=[] 表示约束为空集，结果必为空。"""
    assert resolve_operation_target_paths(
        ["a.mp4"], ["a.mp4"], scope_paths=[]
    ) == []


def test_path_key_matches_case_variant_for_scope():
    """规范化键应让不同写法的同一路径在 scope 内命中。"""
    p1 = r"D:\Editing\视频素材\test\op_target_a.mp4"
    # 候选用 abspath；scope 用 path_status_key 等价键对应的另一写法
    abs_p = os.path.abspath(p1)
    # 故意混用斜杠（Windows normcase/abspath 后应一致）
    alt = abs_p.replace("\\", "/") if os.sep == "\\" else abs_p
    selected = [alt]
    scope = [abs_p]
    targets = resolve_operation_target_paths(selected, [], scope_paths=scope)
    assert len(targets) == 1
    assert path_status_key(targets[0]) == path_status_key(abs_p)


def test_skips_empty_path_strings():
    assert resolve_operation_target_paths(
        ["", "a.mp4", ""], ["x.mp4"]
    ) == ["a.mp4"]
    assert resolve_operation_target_paths(
        None, ["", "b.mp4"]
    ) == ["b.mp4"]