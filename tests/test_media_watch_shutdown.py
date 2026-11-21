"""监控停止超时必须显式失败，不能销毁仍需恢复的分析池。"""
import threading
from types import SimpleNamespace

import pytest

import core.media_watch as media_watch


class BlockedObserver(threading.Thread):
    def __init__(self, entered, release):
        super().__init__(target=lambda: (entered.set(), release.wait()), daemon=True)
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()


@pytest.mark.parametrize('blocked_part', ['monitor', 'observer'])
def test_stop_timeout_preserves_analysis_pool_until_thread_finishes(monkeypatch, blocked_part):
    monkeypatch.setattr(media_watch, 'SHUTDOWN_JOIN_SECONDS', 0.02, raising=False)
    monitor = media_watch.MediaDirectoryMonitor(SimpleNamespace(db=object()), {'directories': []})
    entered, release = threading.Event(), threading.Event()
    if blocked_part == 'monitor':
        blocked = threading.Thread(target=lambda: (entered.set(), release.wait()), daemon=True)
        monitor._thread = blocked
    else:
        blocked = BlockedObserver(entered, release)
        monitor._observer = blocked
    blocked.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(TimeoutError, match='仍在停止.*稍后重试'):
            monitor.stop(shutdown_analysis=True)
        assert blocked.is_alive(), '必须保留实际未结束线程，不能以测试替身隐藏存活状态'
        assert monitor._stop.is_set()
        assert not monitor._analysis_shutdown
        assert not monitor._cancel_batch.is_set()
        assert monitor._analysis_pool.submit(lambda: 'pool preserved').result(timeout=2) == 'pool preserved'
        if blocked_part == 'observer':
            assert blocked.stop_requested.is_set()
        release.set()
        blocked.join(timeout=2)
        assert not blocked.is_alive()
        monitor.stop(shutdown_analysis=True)
        assert monitor._analysis_shutdown
        assert not any(worker.is_alive() for worker in monitor._analysis_pool._threads)
        with pytest.raises(RuntimeError, match='shutdown'):
            monitor._analysis_pool.submit(lambda: None)
    finally:
        release.set()
        blocked.join(timeout=2)
        monitor.stop(shutdown_analysis=True)


def test_nonblocking_stop_keeps_existing_behavior(monkeypatch):
    monkeypatch.setattr(media_watch, 'SHUTDOWN_JOIN_SECONDS', 0.02, raising=False)
    monitor = media_watch.MediaDirectoryMonitor(SimpleNamespace(db=object()), {'directories': []})
    entered, release = threading.Event(), threading.Event()
    blocked = threading.Thread(target=lambda: (entered.set(), release.wait()), daemon=True)
    monitor._thread = blocked
    blocked.start()
    assert entered.wait(timeout=2)
    try:
        monitor.stop(wait=False, shutdown_analysis=True)
        assert blocked.is_alive()
        assert monitor._analysis_shutdown
    finally:
        release.set()
        blocked.join(timeout=2)
        monitor.stop(shutdown_analysis=True)
