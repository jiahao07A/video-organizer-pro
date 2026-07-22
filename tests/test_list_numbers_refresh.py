# -*- coding: utf-8 -*-
"""列表序号刷新防递归。"""
from gui.models.video_table import VideoTableModel, COL_LIST_NO


def test_set_list_numbers_skips_identical_emit():
    m = VideoTableModel([{"path": "a.mp4", "filename": "a.mp4"}])
    emissions = []

    def on_changed(*_a):
        emissions.append(1)

    m.dataChanged.connect(on_changed)
    m.set_list_numbers({0: 1})
    assert len(emissions) == 1
    m.set_list_numbers({0: 1})  # 相同映射不应再 emit
    assert len(emissions) == 1
    m.set_list_numbers({0: 2})
    assert len(emissions) == 2
    assert m.data(m.index(0, COL_LIST_NO)) == 2


def test_set_list_numbers_empty_videos_no_crash():
    m = VideoTableModel([])
    m.set_list_numbers({})
    m.set_list_numbers({0: 1})