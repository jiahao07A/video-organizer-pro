"""真实 SDK/回环 HTTP 验证瞬时状态的调用重试与预算。"""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from openai import OpenAI

from core.ai_gateway import AiRequestGateway
from core.analysis_job_policy import RetryConfig, is_retriable


@contextmanager
def local_api(statuses, cancel_on_first=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            status = statuses[min(len(requests) - 1, len(statuses) - 1)]
            if status == 200:
                body = {"id": "local", "object": "chat.completion", "created": 1,
                        "model": "synthetic", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"ok":true}'}}]}
            else:
                body = {"error": {"message": "Synthetic upstream failure", "type": "api_error", "code": str(status)}}
            payload = json.dumps(body).encode("utf-8")
            if cancel_on_first is not None and len(requests) == 1:
                cancel_on_first.set()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with OpenAI(api_key="synthetic-local-only", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    max_retries=0, timeout=3.0) as client:
            yield client, requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()


def request(gateway, client, extra=1, cancel_event=None):
    return gateway.request_chat(model="synthetic", system_prompt="Return JSON.",
        content_parts=[{"type": "text", "text": "synthetic input"}], client=client,
        retry_config=RetryConfig(call_extra_attempts=extra, backoff_base_sec=0),
        cancel_event=cancel_event)


@pytest.mark.parametrize("status", [408, 500])
def test_transient_http_recovers_once_through_real_sdk(status):
    gateway = AiRequestGateway({})
    with local_api([status, 200]) as (client, requests):
        assert request(gateway, client) == {"ok": True}
        assert len(requests) == 2
        assert gateway.get_last_failure() is None


@pytest.mark.parametrize("status", [408, 500])
@pytest.mark.parametrize("extra", [0, 2])
def test_transient_http_failure_stops_at_call_budget(status, extra):
    gateway = AiRequestGateway({})
    with local_api([status]) as (client, requests):
        assert request(gateway, client, extra=extra) is None
        assert len(requests) == extra + 1
        assert gateway.get_last_failure().retriable is True


@pytest.mark.parametrize("status", [401, 403])
def test_auth_http_is_not_retried_even_with_available_budget(status):
    gateway = AiRequestGateway({})
    with local_api([status, 200]) as (client, requests):
        assert request(gateway, client, extra=3) is None
        assert len(requests) == 1
        assert gateway.get_last_failure().retriable is False


def test_cancel_after_http_failure_does_not_send_second_request():
    cancel = threading.Event()
    gateway = AiRequestGateway({})
    with local_api([500, 200], cancel_on_first=cancel) as (client, requests):
        assert request(gateway, client, extra=3, cancel_event=cancel) is None
        assert len(requests) == 1
        assert gateway.get_last_failure().cancelled is True


@pytest.mark.parametrize("status", [408, 500])
@pytest.mark.parametrize("shape", ["dict", "exception", "response"])
def test_explicit_status_is_retriable_for_supported_error_shapes(status, shape):
    if shape == "dict":
        error = {"status_code": status, "message": "Synthetic upstream failure"}
    else:
        error = RuntimeError("Synthetic upstream failure")
        if shape == "response":
            error.response = SimpleNamespace(status_code=status)
        else:
            error.status_code = status
    assert is_retriable(error)


@pytest.mark.parametrize("error", [
    {"status_code": 500, "cancelled": True},
    {"status_code": 408, "user_cancelled": True},
    FileNotFoundError("timeout.mp4"), PermissionError("timeout.mp4"),
])
def test_cancellation_and_local_files_are_not_transient_http_errors(error):
    assert not is_retriable(error)
