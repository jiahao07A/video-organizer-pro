"""安装冒烟的启动核对、托盘关闭与正式退出语义。"""
from types import SimpleNamespace

import pytest

import video_organizer_app
from tools import smoke_installer_update as smoke


@pytest.mark.parametrize("busy,preparing", [(True, False), (False, True), (True, True)])
def test_smoke_exit_waits_for_startup_check_and_uses_explicit_exit(monkeypatch, busy, preparing):
    callbacks = []
    exits = []
    monkeypatch.setattr(video_organizer_app, "QTimer", SimpleNamespace(
        singleShot=lambda delay, callback: callbacks.append((delay, callback))))
    window = SimpleNamespace(update_controller=SimpleNamespace(busy=busy),
                             _update_preparing=preparing, _exit_application=lambda: exits.append(True))

    video_organizer_app._schedule_smoke_exit(window)
    delay, attempt = callbacks.pop(0)
    assert delay == 0
    attempt()
    assert not exits
    delay, attempt = callbacks.pop(0)
    assert delay == 250

    window.update_controller.busy = False
    window._update_preparing = False
    attempt()
    assert exits == [True]
    assert callbacks == []


def test_completed_startup_is_accepted_even_if_application_already_exited(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke.UpdateSession, "load", lambda cache: SimpleNamespace(phase="completed"))
    process = SimpleNamespace(poll=lambda: 0, returncode=0, pid=123)
    smoke._wait_for_startup(process, tmp_path)


def test_exit_before_startup_reconciliation_is_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke.UpdateSession, "load", lambda cache: SimpleNamespace(phase="launched"))
    process = SimpleNamespace(poll=lambda: 0, returncode=0, pid=123)
    with pytest.raises(RuntimeError, match="程序启动中退出"):
        smoke._wait_for_startup(process, tmp_path)
