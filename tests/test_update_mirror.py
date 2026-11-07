"""更新网络兜底：本地双源故障注入，不依赖外网。"""
from pathlib import Path

import pytest
from PySide6.QtNetwork import QNetworkAccessManager

from core.app_version import REPOSITORY
from core.software_update import UpdateError, validate_url
from gui.services.update_network import HttpTransfer
from tests.test_software_update_gui import app, serve, wait


def test_mirror_only_accepts_fixed_repository_urls():
    from core.software_update import mirror_url
    official = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100&page=2"
    mirrored = mirror_url(official)
    assert mirrored == "https://gh-proxy.org/" + official
    assert validate_url(mirrored, api=True) == mirrored
    asset = f"https://github.com/{REPOSITORY}/releases/download/v0.0.1-rc2/update.json"
    assert validate_url(mirror_url(asset)) == "https://gh-proxy.org/" + asset


@pytest.mark.parametrize("url", [
    "https://gh-proxy.org/https://api.github.com/user",
    "https://gh-proxy.org/https://github.com/other/repo/releases/download/v1/update.json",
    "https://gh-proxy.org/https://evil.test/file",
    "https://gh-proxy.org/https://objects.githubusercontent.com/file",
    "https://gh-proxy.org.evil.test/https://github.com/a",
    "http://gh-proxy.org/https://github.com/a",
    "https://user@gh-proxy.org/https://github.com/a",
    "https://gh-proxy.org:444/https://github.com/a",
])
def test_untrusted_mirror_paths_rejected(url):
    with pytest.raises((UpdateError, ValueError)):
        validate_url(url, api=True)


@pytest.mark.parametrize("partial", [False, True])
def test_network_failure_uses_mirror_and_restarts_partial_download(app, tmp_path, monkeypatch, partial):
    official = (b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\njunk" if partial else
                b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n")
    primary, sockets1, url1 = serve(app, official)
    backup, sockets2, url2 = serve(app, b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood")
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda url, **_: url)
    monkeypatch.setattr("gui.services.update_network.mirror_url", lambda url: url2)
    manager = QNetworkAccessManager()
    destination = tmp_path / "download.part"
    transfer = HttpTransfer(manager, url1, limit=100, destination=destination)
    sources, results, errors = [], [], []
    transfer.source_changed.connect(sources.append)
    transfer.completed.connect(results.append)
    transfer.failed.connect(errors.append)
    transfer.start()
    wait(app, lambda: bool(results or errors))
    assert not errors
    assert results == [destination]
    assert sources and transfer.using_mirror
    assert destination.read_bytes() == b"good", "官方残留不得拼接到镜像数据"
    primary.close()
    backup.close()


@pytest.mark.parametrize("status", [400, 403, 404])
def test_client_error_does_not_use_mirror(app, monkeypatch, status):
    server, sockets, url = serve(app, f"HTTP/1.1 {status} Failed\r\nContent-Length: 0\r\n\r\n".encode())
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda url, **_: url)
    attempts = []
    monkeypatch.setattr("gui.services.update_network.mirror_url", lambda url: attempts.append(url))
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, url, limit=100)
    errors = []
    transfer.failed.connect(errors.append)
    transfer.start()
    wait(app, lambda: bool(errors))
    assert not attempts
    assert not transfer.using_mirror
    server.close()


def test_mirror_failure_stops_after_one_fallback(app, monkeypatch):
    response = b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n"
    server, sockets, url = serve(app, response)
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda url, **_: url)
    monkeypatch.setattr("gui.services.update_network.mirror_url", lambda url: url)
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, url, limit=100)
    errors, sources = [], []
    transfer.failed.connect(errors.append)
    transfer.source_changed.connect(sources.append)
    transfer.start()
    wait(app, lambda: bool(errors))
    assert len(errors) == 1 and len(sources) == 1
    assert "镜像" in errors[0]
    server.close()


def test_official_success_never_requests_mirror(app, monkeypatch):
    server, sockets, url = serve(app, b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood")
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda url, **_: url)
    attempts = []
    monkeypatch.setattr("gui.services.update_network.mirror_url", lambda url: attempts.append(url))
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, url, limit=100)
    results = []
    transfer.completed.connect(results.append)
    transfer.start()
    wait(app, lambda: bool(results))
    assert results == [b"good"] and not attempts and not transfer.using_mirror
    server.close()


@pytest.mark.parametrize("limited", [b"X-RateLimit-Remaining: 0\r\n", b"Retry-After: 60\r\n"])
def test_explicit_github_rate_limit_403_can_use_mirror(app, monkeypatch, limited):
    primary, sockets1, url1 = serve(app, b"HTTP/1.1 403 Forbidden\r\n" + limited + b"Content-Length: 0\r\n\r\n")
    backup, sockets2, url2 = serve(app, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]")
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda url, **_: url)
    monkeypatch.setattr("gui.services.update_network.mirror_url", lambda url: url2)
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, url1, limit=100)
    results = []
    transfer.completed.connect(results.append)
    transfer.start()
    wait(app, lambda: bool(results))
    assert results == [b"[]"] and transfer.using_mirror
    primary.close()
    backup.close()


@pytest.mark.parametrize("failure", ["cancel", "size", "redirect"])
def test_cancel_and_validation_errors_never_request_mirror(app, tmp_path, monkeypatch, failure):
    responses = {
        "cancel": b"HTTP/1.1 200 OK\r\nContent-Length: 9999\r\n\r\npart",
        "size": b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood",
        "redirect": b"HTTP/1.1 302 Found\r\nLocation: https://evil.test/file\r\nContent-Length: 0\r\n\r\n",
    }
    server, sockets, url = serve(app, responses[failure])
    monkeypatch.setattr("gui.services.update_network.validate_url", lambda value, **kwargs: value if value == url else validate_url(value, **kwargs))
    attempts = []
    monkeypatch.setattr("gui.services.update_network.mirror_url", lambda url: attempts.append(url))
    manager = QNetworkAccessManager()
    transfer = HttpTransfer(manager, url, limit=3 if failure == "size" else 9999, destination=tmp_path / "download.part")
    errors = []
    transfer.failed.connect(errors.append)
    transfer.start()
    if failure == "cancel":
        wait(app, lambda: transfer.received > 0)
        transfer.cancel()
    wait(app, lambda: bool(errors))
    assert not attempts and not transfer.using_mirror
    server.close()
