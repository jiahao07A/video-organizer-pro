# -*- coding: utf-8 -*-
"""TagAiWorker：UI 线程不阻塞。"""
from gui.workers.tag_ai_worker import TagAiWorker


def test_tag_ai_worker_run_ok():
    """直接调用 run()（同线程 DirectConnection），不依赖 QApplication 事件循环。"""
    out = []
    err = []
    w = TagAiWorker(lambda: {"ok": True, "n": 1})
    w.finished_ok.connect(out.append)
    w.failed.connect(err.append)
    w.run()
    assert err == []
    assert out and out[0]["ok"] is True


def test_tag_ai_worker_run_fail():
    out = []
    err = []
    w = TagAiWorker(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    w.finished_ok.connect(out.append)
    w.failed.connect(err.append)
    w.run()
    assert out == []
    assert err and "boom" in err[0]