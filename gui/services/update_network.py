"""Qt 异步 HTTP：限额、超时、人工重定向校验与下载取消。"""
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from core.software_update import UpdateError, mirror_url, validate_url


class HttpTransfer(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    source_changed = Signal(str)

    def __init__(self, manager: QNetworkAccessManager, url: str, *, limit: int, destination: Path = None, parent=None):
        super().__init__(parent)
        self.manager, self.url, self.limit = manager, url, limit
        self.original_url = url
        self.using_mirror = False
        self.destination = destination
        self.reply = None
        self.handle = None
        self.data = bytearray()
        self.received = 0
        self.redirects = 0
        self.error = ""
        self.cancelled = False

    def start(self):
        try:
            validate_url(self.url, api=True)
            if self.destination:
                self.handle = self.destination.open("wb")
            self._request()
        except (OSError, UpdateError) as exc:
            self._fail(str(exc))

    def _request(self):
        validate_url(self.url, api=True)
        request = QNetworkRequest(QUrl(self.url))
        request.setHeader(QNetworkRequest.UserAgentHeader, "VideoOrganizerPro-Updater/1")
        request.setRawHeader(b"Accept", b"application/octet-stream" if self.destination else b"application/vnd.github+json")
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        request.setTransferTimeout(30000)
        self.reply = self.manager.get(request)
        self.reply.readyRead.connect(self._read)
        self.reply.finished.connect(self._finished)

    def _read(self):
        # 重定向响应体不得写入最终文件。
        status = self.reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        chunk = bytes(self.reply.readAll())
        if status != 200:
            return
        try:
            if self.received + len(chunk) > self.limit:
                raise UpdateError("更新响应超过允许大小")
            if self.handle:
                self.handle.write(chunk)
            else:
                self.data.extend(chunk)
            self.received += len(chunk)
            self.progress.emit(self.received, self.limit)
        except (OSError, UpdateError) as exc:
            self.error = str(exc)
            self.reply.abort()

    def _finished(self):
        reply = self.reply
        self._read()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        redirect = reply.attribute(QNetworkRequest.RedirectionTargetAttribute)
        if redirect and status in (301, 302, 303, 307, 308) and not self.cancelled:
            self.redirects += 1
            try:
                if self.redirects > 5:
                    raise UpdateError("更新地址重定向过多")
                self.url = reply.url().resolved(redirect).toString()
                validate_url(self.url, api=True)
                reply.deleteLater()
                self._request()
                return
            except UpdateError as exc:
                self.error = str(exc)
        if self._try_mirror(reply, status):
            return
        if self.handle:
            try:
                self.handle.close()
            except OSError as exc:
                self.error = str(exc)
            self.handle = None
        error = self.error
        if self.cancelled:
            error = "下载已取消，可重新下载"
        elif not error and (status != 200 or reply.error() != QNetworkReply.NoError):
            error = f"更新请求失败（HTTP {status or '无响应'}）：{reply.errorString()}"
        reply.deleteLater()
        self.reply = None
        if error:
            if self.using_mirror:
                error = "GitHub 与国内镜像均未完成请求：" + error
            self._fail(error)
        else:
            self.completed.emit(self.destination if self.destination else bytes(self.data))

    def _try_mirror(self, reply, status):
        if self.cancelled or self.error or self.using_mirror:
            return False
        network_errors = {
            QNetworkReply.ConnectionRefusedError, QNetworkReply.RemoteHostClosedError,
            QNetworkReply.HostNotFoundError, QNetworkReply.TimeoutError,
            QNetworkReply.TemporaryNetworkFailureError, QNetworkReply.NetworkSessionFailedError,
            QNetworkReply.UnknownNetworkError, QNetworkReply.ProxyConnectionRefusedError,
            QNetworkReply.ProxyConnectionClosedError, QNetworkReply.ProxyNotFoundError,
            QNetworkReply.ProxyTimeoutError,
        }
        limited = status == 403 and (
            bytes(reply.rawHeader("X-RateLimit-Remaining")) == b"0"
            or bool(reply.rawHeader("Retry-After"))
        )
        if not (status == 429 or limited or status in (500, 502, 503, 504)
                or reply.error() in network_errors):
            return False
        try:
            target = mirror_url(self.original_url)
        except (UpdateError, ValueError):
            return False
        self.using_mirror = True
        reply.deleteLater()
        self.reply = None
        try:
            if self.handle:
                self.handle.close()
                self.handle = None
            # 首版不续传，明确截断同一个 part 文件，避免拼接两源的数据。
            if self.destination:
                self.handle = self.destination.open("wb")
            self.url = target
            self.received = 0
            self.data.clear()
            self.redirects = 0
            self.source_changed.emit("gh-proxy.org")
            self.progress.emit(0, self.limit)
            self._request()
        except (OSError, UpdateError) as exc:
            self._fail(f"镜像请求准备失败：{exc}")
        return True

    def cancel(self):
        self.cancelled = True
        if self.reply:
            self.reply.abort()

    def _fail(self, message):
        if self.handle:
            try:
                self.handle.close()
            except OSError as exc:
                message += f"；关闭临时文件失败：{exc}"
            finally:
                self.handle = None
        try:
            if self.destination and self.destination.is_file():
                self.destination.unlink()  # 单个明确的未完成下载文件。
        except OSError as exc:
            # Windows 上只读属性或杀毒软件占用会阻止清理；仍须结束下载状态，
            # 允许用户重试和正常退出，不得因清理失败丢失 failed 信号。
            message += f"；临时文件已保留，清理失败：{exc}"
        self.failed.emit(message)
