"""Qt 更新网络、状态机和安装门禁测试（仅隔离数据）。"""
from tests.support.qt_http import wait, serve
import json
import time
from pathlib import Path
from types import SimpleNamespace


import pytest
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtWidgets import QMessageBox

from core.software_update import ReleaseInfo, UpdateError, UpdateState
from gui.services.update_controller import UpdateController
from gui.services.update_network import HttpTransfer
from tests.support.updates import signed as signed  # pytest 签名夹具；每次使用临时密钥。
from tests.support.updates import candidate


@pytest.fixture
def controller(app, tmp_path, monkeypatch):
    monkeypatch.setattr("gui.services.update_controller.update_cache", lambda: tmp_path / "cache")
    return UpdateController(SimpleNamespace(manual_test_environment=True))


def test_background_checks_and_manual_skip_visibility(controller, monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "check", lambda manual=True: calls.append(manual))
    controller.automatic_check()
    assert calls == [False]
    controller.preferences["last_check"] = time.time()
    controller.automatic_check()
    assert calls == [False]
    controller.preferences.update(last_check=0, auto_check=False)
    controller.automatic_check()
    assert calls == [False]


def test_signed_candidate_skip_and_manual_notice(controller, signed):
    raw, signature = signed.encode()
    controller.raw = raw
    controller.preferences["skipped"] = "0.0.3"
    notices = []
    controller.available.connect(lambda: notices.append(True))
    controller._got_manifest(signature, {"tag_name": "v0.0.3"})
    assert controller.state == UpdateState.AVAILABLE
    assert notices == []
    controller._manual = True
    controller._got_manifest(signature, {"tag_name": "v0.0.3"})
    assert notices == [True]


@pytest.mark.parametrize("lower_compatible,cached", [(True, False), (False, False), (False, True)])
def test_check_skips_signed_release_for_other_platform(controller, signed, monkeypatch, lower_compatible, cached):
    if cached:
        controller.raw, controller.signature = signed.encode()
        controller.release = ReleaseInfo.from_signed(controller.raw, controller.signature)
        directory = controller.release_directory()
        directory.mkdir(parents=True)
        part = directory / "download.part"
        part.write_bytes(signed.installer.read_bytes())
        controller._promote(part)
    assets = {}
    requested = []
    releases = [candidate("v0.0.3"), candidate("v0.0.2")]
    for release in releases:
        value = release["tag_name"].removeprefix("v")
        name = f"VideoOrganizer-v{value}-setup-win-x64.exe"
        prefix = release["assets"][0]["browser_download_url"].removesuffix(name)
        raw, sig = signed.encode(
            version=value, filename=name, url=prefix + name,
            platform="windows-x64" if lower_compatible and value == "0.0.2" else "windows-arm64",
        )
        assets[prefix + "update.json"] = raw
        assets[prefix + "update.json.sig"] = sig
    def fetch(url, limit, completed, destination=None):
        requested.append(url)
        try:
            completed(assets[url])
        except UpdateError as exc:
            controller._fail(str(exc))
    monkeypatch.setattr(controller, "_fetch", fetch)
    monkeypatch.setattr(controller, "_fetch_page", lambda: controller._got_page(json.dumps(releases).encode()))
    controller.check()
    assert len(requested) == 4, "平台不适用时应继续检查后续候选"
    if lower_compatible:
        assert controller.state == UpdateState.AVAILABLE
        assert controller.release.version == "0.0.2"
    elif cached:
        assert controller.state == UpdateState.READY
        assert controller.release.version == "0.0.3"
        assert controller.raw == signed.encode()[0]
    else:
        assert controller.state == UpdateState.IDLE
        assert controller.release is None


@pytest.mark.parametrize("invalid", ["signature", "schema"])
def test_invalid_manifest_does_not_fall_back_to_lower_release(controller, signed, monkeypatch, invalid):
    releases = [candidate("v0.0.3"), candidate("v0.0.2")]
    raw, sig = signed.encode(platform="windows-arm64", **({"schema": 99} if invalid == "schema" else {}))
    if invalid == "signature":
        raw += b" "
    requested = []
    def fetch(url, limit, completed, destination=None):
        requested.append(url)
        try:
            completed(sig if url.endswith(".sig") else raw)
        except UpdateError as exc:
            controller._fail(str(exc))
    monkeypatch.setattr(controller, "_fetch", fetch)
    controller._got_page(json.dumps(releases).encode())
    assert controller.state == UpdateState.FAILED
    assert controller.release is None
    assert len(requested) == 2, "不可信或格式损坏的清单不得静默跳过"


def test_offline_import_and_cached_revalidation(app, controller, signed):
    raw, sig = signed.encode()
    manifest = signed.installer.parent / "update.json"
    manifest.write_bytes(raw)
    manifest.with_name("update.json.sig").write_bytes(sig)
    controller.import_offline(manifest)
    wait(app, lambda: controller.state in (UpdateState.READY, UpdateState.FAILED))
    assert controller.state == UpdateState.READY, controller.message
    destination = controller.release_directory() / controller.release.filename
    assert destination.read_bytes() == signed.installer.read_bytes()
    restored = UpdateController(SimpleNamespace())
    assert restored.state == UpdateState.READY
    # 修改缓存后，准备阶段必须重新验包，不能信任 ready.json。
    destination.write_bytes(b"corrupt")
    controller.prepare()
    wait(app, lambda: controller.state == UpdateState.FAILED)
    assert "大小" in controller.message


def test_offline_tamper_does_not_become_ready(app, controller, signed):
    raw, sig = signed.encode()
    manifest = signed.installer.parent / "update.json"
    manifest.write_bytes(raw + b" ")
    manifest.with_name("update.json.sig").write_bytes(sig)
    controller.import_offline(manifest)
    wait(app, lambda: controller.state == UpdateState.FAILED)
    assert "签名" in controller.message
    assert not (controller.cache / "ready.json").exists()


@pytest.mark.parametrize("response,limit,expected", [
    (b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood", 4, b"good"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood", 3, None),
    (b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n", 10, None),
])
def test_qt_transfer_limits_and_http_failure(app, monkeypatch, response, limit, expected):
    # 仅测试本地 HTTP 传输；生产 URL 校验另有拒绝测试。
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda url, **_: url)
    server, sockets, url = serve(app, response)
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, url, limit=limit)
    results, failures = [], []
    transfer.completed.connect(results.append)
    transfer.failed.connect(failures.append)
    transfer.start()
    wait(app, lambda: bool(results or failures))
    if expected:
        assert results == [expected]
    else:
        assert failures and not results
    server.close()


def test_cancel_download_removes_only_partial_file(app, tmp_path, monkeypatch):
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda url, **_: url)
    server, sockets, url = serve(app, b"HTTP/1.1 200 OK\r\nContent-Length: 9999\r\n\r\npart")
    manager = QNetworkAccessManager()
    part = tmp_path / "installer.part"
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep")
    transfer = HttpTransfer(manager, url, limit=9999, destination=part)
    errors = []
    transfer.failed.connect(errors.append)
    transfer.start()
    wait(app, lambda: transfer.received > 0)
    transfer.cancel()
    wait(app, lambda: bool(errors))
    assert "取消" in errors[0]
    assert not part.exists() and unrelated.read_text() == "keep"
    server.close()


def test_redirect_to_untrusted_host_is_rejected(app, monkeypatch):
    from core.software_update import validate_url
    server, sockets, url = serve(app, b"HTTP/1.1 302 Found\r\nLocation: https://evil.test/file\r\nContent-Length: 0\r\n\r\n")
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda value, **kwargs: value if value == url else validate_url(value, **kwargs))
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, url, limit=100)
    errors = []
    transfer.failed.connect(errors.append)
    transfer.start()
    wait(app, lambda: bool(errors))
    assert "官方" in errors[0]
    server.close()


def test_settings_tab_and_task_gate(app, tmp_path, monkeypatch, signed):
    from core.video_organizer_service import VideoOrganizerService
    from gui.main_window import MainWindow
    monkeypatch.setattr("gui.services.update_controller.update_cache", lambda: tmp_path / "cache")
    monkeypatch.setattr("core.update_runtime.is_installed", lambda: True)
    monkeypatch.setattr("gui.views.settings._window_has_active_background_work", lambda _: True)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    service = VideoOrganizerService(db_path=str(tmp_path / "db.sqlite"), results_json=str(tmp_path / "results.json"))
    window = MainWindow(service)
    page = window._ensure_page(3)
    assert page.tabs.tabText(page._page_index_by_id["system"]) == "数据与维护"
    assert page.focus_setting("software_update")
    assert page.tabs.currentIndex() == page._page_index_by_id["system"]
    window.update_controller.release = ReleaseInfo.from_signed(*signed.encode())
    window.update_controller.state = UpdateState.READY
    window.install_update()
    assert warnings and "先完成" in warnings[-1]
    assert not window._update_preparing
    monkeypatch.setattr("gui.views.settings._window_has_active_background_work", lambda _: False)
    window.close()
    window.deleteLater()
    app.processEvents()


def test_update_installer_start_failure_is_reported_without_exit(app, tmp_path, monkeypatch):
    from gui.main_window import MainWindow
    from core.software_update import UpdateState
    from PySide6.QtWidgets import QWidget
    monkeypatch.setattr('gui.services.update_controller.update_cache', lambda: tmp_path / 'cache')
    controller = UpdateController(SimpleNamespace(manual_test_environment=True))
    controller.state = UpdateState.READY
    controller.release = SimpleNamespace(filename='never-execute.exe', version='0.0.2')
    controller.release_directory().mkdir(parents=True, exist_ok=True)
    marker = controller.cache / 'backup-marker.json'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('preserve', encoding='utf-8')
    window = MainWindow.__new__(MainWindow)
    from PySide6.QtWidgets import QMainWindow
    QMainWindow.__init__(window)
    window.update_controller = controller
    window.setCentralWidget(QWidget(window))
    window._update_preparing = True
    window._update_exiting = False
    window._update_notice = None
    window.centralWidget().setEnabled(False)
    controller.changed.connect(window._update_state_changed)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    monkeypatch.setattr('gui.views.settings._window_has_active_background_work', lambda _: False)
    monkeypatch.setattr('core.update_runtime.launch_installer', lambda *_: (_ for _ in ()).throw(OSError('synthetic start failure')))
    window._launch_update(SimpleNamespace())
    assert controller.state == UpdateState.FAILED
    assert 'synthetic start failure' in controller.message
    assert not window._update_preparing
    assert window.centralWidget().isEnabled()
    assert not window._update_exiting
    assert marker.read_text(encoding='utf-8') == 'preserve'
    assert window.isVisible() is False
    window.deleteLater()


def test_update_requires_successful_autosave_flush(app, tmp_path, monkeypatch):
    from core.video_organizer_service import SettingsManager, VideoOrganizerService
    from gui.views.settings import SettingsView
    service = VideoOrganizerService(db_path=str(tmp_path / "db.sqlite"), results_json=str(tmp_path / "results.json"))
    view = SettingsView(service)
    expected = view.font_spin.value() + 1
    view.font_spin.setValue(expected)
    original_writer = SettingsManager.save_settings
    monkeypatch.setattr(SettingsManager, "save_settings", lambda *args: False)
    assert not view.save_before_update()
    assert view.prefs_controller._dirty
    assert view.font_spin.value() == expected
    monkeypatch.setattr(SettingsManager, "save_settings", original_writer)
    assert view.save_before_update()
    assert not view.prefs_controller._dirty
    assert SettingsManager.load_settings(service.db)["ui_preferences"]["font_size"] == expected
    view.deleteLater()
    app.processEvents()


def test_recheck_preserves_downloaded_update(controller, signed, monkeypatch):
    raw, sig = signed.encode()
    controller.release = ReleaseInfo.from_signed(raw, sig)
    controller.raw, controller.signature = raw, sig
    directory = controller.release_directory()
    directory.mkdir(parents=True)
    part = directory / "download.part"
    part.write_bytes(signed.installer.read_bytes())
    controller._promote(part)
    monkeypatch.setattr(controller, "_fetch_page", lambda: None)
    controller.check()
    controller.raw = raw
    controller._got_manifest(sig, {"tag_name": "v0.0.3"})
    assert controller.state == UpdateState.READY, "同一版本已下载，不应要求再次下载"
    controller.check()
    controller._fail("网络不可用")
    assert controller.state == UpdateState.READY, "断网不应阻止安装此前验证过的缓存"
    assert "网络不可用" in controller.message


def test_readonly_partial_file_reports_failure_instead_of_stalling(app, tmp_path, monkeypatch):
    from core.app_version import REPOSITORY
    part = tmp_path / "installer.part"
    part.write_bytes(b"previous partial download")
    original_open, original_unlink = Path.open, Path.unlink
    def denied_open(path, *args, **kwargs):
        if path == part:
            raise PermissionError("partial file is read-only")
        return original_open(path, *args, **kwargs)
    def denied_unlink(path, *args, **kwargs):
        if path == part:
            raise PermissionError("partial file is locked")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied_open)
    monkeypatch.setattr(Path, "unlink", denied_unlink)
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, f"https://github.com/{REPOSITORY}/releases/download/v0.0.3/installer.exe", limit=100, destination=part)
    errors = []
    transfer.failed.connect(errors.append)
    transfer.start()
    assert errors and "read-only" in errors[0]
    assert part.exists(), "无法清理时应保留单个临时文件，不能让状态机卡住"
