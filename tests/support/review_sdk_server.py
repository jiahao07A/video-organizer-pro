"""真实 SDK 请求的回环端点；测试只使用虚构凭据和合成内容。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


@contextmanager
def sdk_server():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, data, content_type="application/json"):
            body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization")))
            self.reply({"data": [{"id": "candidate-a"}, {"id": "candidate-b"}]})

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path, body))
            if self.path.endswith("/audio/transcriptions"):
                self.reply("合成转录", "text/plain; charset=utf-8")
            else:
                content = {"category": "Other", "summary": "彩色测试图", "tags": []}
                self.reply({"choices": [{"message": {"content": json.dumps(content)}}]})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", calls
        finally:
            server.shutdown()
            thread.join(timeout=3)
