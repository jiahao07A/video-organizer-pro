"""共享的软件更新协调器；网络在 Qt 事件循环，哈希与备份在工作线程。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtNetwork import QNetworkAccessManager

from core.app_version import APP_VERSION, REPOSITORY
from core.software_update import (
    MAX_MANIFEST_BYTES, ReleaseInfo, UnsupportedUpdatePlatform, UpdateError, UpdateState,
    read_signed_release, release_candidates, require_space, update_cache, verify_installer, version,
)
from core.storage_backup import write_json_atomic
from core.diagnostic_logging import bind_worker, worker_run, worker_exception


class UpdateJob(QThread):
    def __init__(self, operation, parent):
        super().__init__(parent)
        bind_worker(self)
        self.operation = operation
        self.result = None
        self.error = ""

    @worker_run
    def run(self):
        try:
            self.result = self.operation()
        except Exception as exc:
            worker_exception()
            self.error = str(exc) or type(exc).__name__


class UpdateController(QObject):
    changed = Signal()
    available = Signal()
    prepared = Signal(object)
    notice_dismissed = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.cache = update_cache()
        self.state = UpdateState.IDLE
        self.message = "尚未检查更新"
        self.release = None
        self.received = 0
        self.network = QNetworkAccessManager(self)
        self.transfer = None
        self.job = None
        self.raw = self.signature = b""
        self._manual = False
        self._candidates = []
        self._page = 1
        self._releases = []
        self._auto_started = False
        self._checked_ready = False
        self.mirror_used = False
        self.preferences = {"auto_check": True, "preview": False, "last_check": 0, "skipped": "", "snooze_until": 0}
        try:
            path = self.cache / "preferences.json"
            if path.is_file():
                stored = json.loads(path.read_text(encoding="utf-8"))
                self.preferences.update({key: stored[key] for key in self.preferences if key in stored})
            self._validate_preferences()
        except (OSError, ValueError, TypeError):
            self.preferences.update(last_check=0, snooze_until=0, auto_check=True, preview=False, skipped="")
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self.automatic_check)
        self._restore_ready()

    def _validate_preferences(self):
        for key in ("last_check", "snooze_until"):
            if not isinstance(self.preferences[key], (int, float)) or self.preferences[key] < 0:
                raise ValueError("时间配置无效")
        for key in ("auto_check", "preview"):
            if type(self.preferences[key]) is not bool:
                raise ValueError("更新配置无效")

    @property
    def busy(self):
        return self.state in (UpdateState.CHECKING, UpdateState.DOWNLOADING, UpdateState.PREPARING) or bool(self.job and self.job.isRunning())

    def _set(self, state, message):
        from core.diagnostic_logging import event
        event('update.state', state=str(state), message=message)
        self.state, self.message = state, message
        self.changed.emit()

    def _save_preferences(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self.cache / "preferences.json"), self.preferences)

    def set_preference(self, key, value):
        previous = self.preferences.copy()
        self.preferences[key] = value
        try:
            self._save_preferences()
        except OSError as exc:
            self.preferences = previous
            self._fail(f"更新偏好保存失败：{exc}")
            return
        if key == "preview":
            self.release = None
            self._set(UpdateState.IDLE, "渠道已切换，请检查更新；不会自动降级")
        else:
            self.changed.emit()

    def start_automatic_checks(self):
        if self._auto_started:
            return
        self._auto_started = True
        if getattr(self.service, "manual_test_environment", False) or not getattr(sys, "frozen", False):
            return
        self._timer.start()
        QTimer.singleShot(10_000, self.automatic_check)

    def automatic_check(self):
        now = time.time()
        if self.preferences["auto_check"] and not self.busy and now - self.preferences["last_check"] >= 86400:
            self.check(manual=False)

    def _fetch(self, url, limit, completed, destination=None):
        from gui.services.update_network import HttpTransfer
        transfer = HttpTransfer(self.network, url, limit=limit, destination=destination, parent=self)
        self.transfer = transfer
        self.changed.emit()
        def done(result):
            self.transfer = None
            transfer.deleteLater()
            try:
                completed(result)
            except (UpdateError, OSError, ValueError, TypeError, KeyError) as exc:
                self._fail(str(exc))
        def failed(message):
            self.transfer = None
            transfer.deleteLater()
            self._fail(message)
        transfer.completed.connect(done)
        transfer.failed.connect(failed)
        transfer.progress.connect(self._progress)
        transfer.source_changed.connect(self._using_mirror)
        transfer.start()

    def _using_mirror(self, source):
        self.mirror_used = True
        self.message = f"GitHub 网络请求失败，正在使用国内镜像 {source} 重试…"
        self.changed.emit()

    def _progress(self, received, total):
        if self.state == UpdateState.DOWNLOADING:
            self.received = received
            self.changed.emit()

    def _fail(self, message):
        if self.state == UpdateState.CHECKING and self._checked_ready:
            # 在线检查失败不撤销此前已下载的可信安装包。
            self._checked_ready = False
            if self._restore_ready():
                self._set(UpdateState.READY, f"{message}；已下载的更新仍可安装，安装前会再次校验")
                return
        self._set(UpdateState.FAILED, message)

    def check(self, manual=True):
        if self.busy:
            return
        self._checked_ready = self.state == UpdateState.READY
        self._manual = manual
        self.mirror_used = False
        self._page, self._releases = 1, []
        self.preferences["last_check"] = time.time()
        try:
            self._save_preferences()
        except OSError as exc:
            self._fail(str(exc))
            return
        self.release = None
        self._set(UpdateState.CHECKING, "正在检查 GitHub 官方发布…")
        self._fetch_page()

    def _fetch_page(self):
        self._fetch(f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100&page={self._page}", 2 * 1024**2, self._got_page)

    def _got_page(self, raw):
        page = json.loads(raw)
        if not isinstance(page, list):
            raise UpdateError("发布列表格式无效")
        self._releases.extend(page)
        if len(page) == 100 and self._page < 10:
            self._page += 1
            self._fetch_page()
            return
        self._candidates = release_candidates(self._releases, preview=self.preferences["preview"])
        self._fetch_candidate()

    def _fetch_candidate(self):
        if not self._candidates:
            if self._checked_ready and self._restore_ready():
                self._checked_ready = False
                self._set(UpdateState.READY, "未发现其他适用版本；已下载的更新仍可安装")
                return
            self._set(UpdateState.IDLE, "未发现适用且带完整更新资产的新版本；历史版本可在官网下载页查看")
            return
        candidate = self._candidates.pop(0)
        assets = {item["name"]: item["browser_download_url"] for item in candidate["assets"]}
        def manifest(raw):
            self.raw = raw
            self._fetch(assets["update.json.sig"], 64, lambda sig: self._got_manifest(sig, candidate))
        self._fetch(assets["update.json"], MAX_MANIFEST_BYTES, manifest)

    def _got_manifest(self, signature, candidate):
        try:
            release = ReleaseInfo.from_signed(self.raw, signature)
        except UnsupportedUpdatePlatform:
            # 仅跳过已经验签的其他平台；签名、格式等错误仍由请求层报告失败。
            self._fetch_candidate()
            return
        if version(release.version) != version(candidate["tag_name"]):
            raise UpdateError("发布标签与签名清单版本不一致")
        if not self.preferences["preview"] and release.channel != "stable":
            raise UpdateError("当前渠道不接受测试版本")
        self.signature, self.release = signature, release
        self._checked_ready = False
        if self._restore_ready(expected=release):
            self._set(UpdateState.READY, f"版本 {release.version} 已下载，可以安装并重启")
        else:
            self._set(UpdateState.AVAILABLE, f"发现新版本 {release.version}")
        if self._manual or (release.version != self.preferences["skipped"] and time.time() >= self.preferences["snooze_until"]):
            self.available.emit()

    def snooze(self):
        self.set_preference("snooze_until", time.time() + 86400)
        self.notice_dismissed.emit()

    def skip(self):
        if self.release:
            self.set_preference("skipped", self.release.version)
            self.notice_dismissed.emit()

    def _run(self, operation, callback):
        job = UpdateJob(operation, self)
        self.job = job
        def finished():
            self.job = None
            job.deleteLater()
            if job.error:
                self._fail(job.error)
            else:
                try:
                    callback(job.result)
                except (OSError, ValueError, UpdateError) as exc:
                    self._fail(str(exc))
        job.finished.connect(finished)
        job.start()

    def release_directory(self):
        return self.cache / self.release.version

    def download(self):
        if self.busy or not self.release:
            return
        self.mirror_used = False
        try:
            directory = self.release_directory()
            directory.mkdir(parents=True, exist_ok=True)
            require_space(directory, self.release.size + 16 * 1024**2)
            self.received = 0
            self._set(UpdateState.DOWNLOADING, "正在下载官方安装包…")
            part = directory / (self.release.filename + ".part")
            self._fetch(self.release.url, self.release.size, lambda path: self._verify_download(path), part)
        except (OSError, UpdateError) as exc:
            self._fail(str(exc))

    def _verify_download(self, path):
        self._set(UpdateState.DOWNLOADING, "正在校验安装包…")
        self._run(lambda: verify_installer(path, self.release), lambda _: self._promote(path))

    def _promote(self, path):
        target = self.release_directory() / self.release.filename
        os.replace(path, target)
        (self.release_directory() / "update.json").write_bytes(self.raw)
        (self.release_directory() / "update.json.sig").write_bytes(self.signature)
        write_json_atomic(str(self.cache / "ready.json"), {"version": self.release.version})
        self._set(UpdateState.READY, "安装包已验证，可以安装并重启；也可稍后安装")

    def _restore_ready(self, expected=None):
        try:
            pointer = json.loads((self.cache / "ready.json").read_text(encoding="utf-8"))
            value = pointer["version"]
            if version(value) <= version(APP_VERSION):
                return
            directory = self.cache / value
            release, raw, sig = read_signed_release(directory)
            if expected is not None and release != expected:
                return False
            if release.version != value or not (directory / release.filename).is_file():
                return
            if not self.preferences["preview"] and release.channel != "stable":
                return
            self.release, self.raw, self.signature = release, raw, sig
            self._set(UpdateState.READY, "已有待安装更新；安装前会再次验证")
            return True
        except (OSError, ValueError, KeyError, TypeError, UpdateError):
            return False

    def import_offline(self, manifest: Path):
        if self.busy:
            return
        self.mirror_used = False
        self._set(UpdateState.DOWNLOADING, "正在校验离线更新包…")
        def operation():
            if manifest.stat().st_size > MAX_MANIFEST_BYTES:
                raise UpdateError("清单过大")
            raw = manifest.read_bytes()
            sig_path = manifest.with_name("update.json.sig")
            if sig_path.stat().st_size != 64:
                raise UpdateError("签名大小无效")
            sig = sig_path.read_bytes()
            release = ReleaseInfo.from_signed(raw, sig)
            if version(release.version) <= version(APP_VERSION):
                raise UpdateError("离线更新必须比当前版本更新")
            if release.channel == "preview" and not self.preferences["preview"]:
                raise UpdateError("请先开启接收测试版本")
            source = manifest.parent / release.filename
            verify_installer(source, release)
            directory = self.cache / release.version
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (release.filename + ".part")
            if source.resolve() == target.resolve():
                raise UpdateError("请选择官方下载文件所在目录")
            require_space(directory, release.size + 16 * 1024**2)
            shutil.copyfile(source, target)
            verify_installer(target, release)
            return release, raw, sig, target
        def done(result):
            self.release, self.raw, self.signature, target = result
            self._promote(target)
        self._run(operation, done)

    def cancel_download(self):
        if self.transfer and self.state == UpdateState.DOWNLOADING:
            self.transfer.cancel()

    def prepare(self):
        if self.busy or self.state != UpdateState.READY:
            return
        from core.update_runtime import prepare_install
        self._set(UpdateState.PREPARING, "正在复验安装包并备份管理数据，请勿关闭…")
        def operation():
            directory = self.release_directory()
            release, _, _ = read_signed_release(directory)
            if release != self.release:
                raise UpdateError("缓存清单已变化，请重新下载")
            return prepare_install(self.service, release, directory / release.filename, self.cache)
        self._run(operation, self.prepared.emit)
