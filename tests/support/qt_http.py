"""共享隔离测试辅助。"""
import time
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtTest import QTest


def wait(app, predicate, timeout=4000):
    end = time.monotonic() + timeout / 1000
    while time.monotonic() < end and not predicate():
        app.processEvents()
        QTest.qWait(10)
    assert predicate()

def serve(app, response):
    server = QTcpServer()
    assert server.listen(QHostAddress.LocalHost, 0)
    sockets = []
    def connected():
        socket = server.nextPendingConnection()
        sockets.append(socket)
        def request():
            socket.readAll()
            socket.write(response)
            socket.flush()
            if b"Content-Length: 9999" not in response:
                socket.disconnectFromHost()
        socket.readyRead.connect(request)
    server.newConnection.connect(connected)
    return server, sockets, f"http://127.0.0.1:{server.serverPort()}/asset"
