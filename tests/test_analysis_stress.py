"""本地真实HTTP压力测试；设置VOP_RUN_STRESS=1后显式执行，不访问外部API。"""
import json
import logging
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest

from core.ai_gateway import AiRequestGateway
from core.analysis_job_policy import RetryConfig
from core.video_organizer_service import SettingsManager, VideoOrganizerService


pytestmark = pytest.mark.skipif(os.environ.get("VOP_RUN_STRESS") != "1", reason="显式启用本地压力测试")
RESULTS = []
NO_RETRY = RetryConfig(call_extra_attempts=0, item_max_attempts=1, batch_rerun_enabled=False)


@pytest.fixture(scope="module", autouse=True)
def save_results():
    yield
    if os.environ.get("VOP_RUN_STRESS") == "1":
        target = Path(__file__).resolve().parents[1] / ".release-build/perf-stress-results.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def local_server(*, barrier=0, delay=0.05, fault=None, held=None):
    lock = threading.Lock()
    gate = threading.Event()
    stats = {"requests": 0, "active": 0, "peak": 0, "attempts": Counter(), "statuses": Counter()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = json.dumps(body["messages"][-1]["content"], sort_keys=True)
            with lock:
                stats["requests"] += 1
                stats["active"] += 1
                stats["peak"] = max(stats["peak"], stats["active"])
                stats["attempts"][key] += 1
                attempt = stats["attempts"][key]
                if stats["active"] >= barrier:
                    gate.set()
            try:
                ready = not barrier or gate.wait(25)
                if held is not None:
                    held.wait(5)
                time.sleep(delay)
                status = 500 if not ready else (fault(attempt) if fault else 200)
                payload = ({"error": {"message": "local simulated failure", "type": "stress"}}
                           if status != 200 else {"id": "local", "choices": [{"message": {"content": json.dumps({"category": "Broll", "summary": "ok", "tags": [], "tag_groups": {}})}}]})
                data = json.dumps(payload).encode()
                with lock:
                    stats["statuses"][status] += 1
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # 短超时测试中客户端先断开是预期行为。
            finally:
                with lock:
                    stats["active"] -= 1

    class Server(ThreadingHTTPServer):
        request_queue_size = 1024
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="stress-http-server", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", stats
    finally:
        gate.set()
        if held is not None:
            held.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        deadline = time.monotonic() + 5
        while stats["active"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not thread.is_alive()
        assert stats["active"] == 0


@contextmanager
def measure(name):
    process = psutil.Process()
    stop = threading.Event()
    start = time.monotonic()
    before = {"rss_mb": process.memory_info().rss / 1048576, "threads": process.num_threads()}
    peaks = dict(before)

    def sample():
        while not stop.wait(0.02):
            peaks["rss_mb"] = max(peaks["rss_mb"], process.memory_info().rss / 1048576)
            peaks["threads"] = max(peaks["threads"], process.num_threads())

    sampler = threading.Thread(target=sample, name="stress-resource-sampler", daemon=True)
    sampler.start()
    result = {"name": name}
    try:
        yield result
    finally:
        stop.set()
        sampler.join(timeout=2)
        result.update(seconds=round(time.monotonic() - start, 3), resources_before=before,
                      resources_peak=peaks, resources_after={"rss_mb": process.memory_info().rss / 1048576,
                                                           "threads": process.num_threads()})
        RESULTS.append(result)


def gateway_settings(url):
    return {"api": {"key": "local-stress-only", "base_url": url}}


def count_real_clients(monkeypatch, gateway):
    original = gateway._client_for_route
    clients = []

    def create(route, timeout):
        client = original(route, timeout)
        clients.append(client)
        return client

    monkeypatch.setattr(gateway, "_client_for_route", create)
    return clients


def request(gateway, index, **kwargs):
    return gateway.request_chat(model="local-test", system_prompt="local stress", content_parts=[{"type": "text", "text": str(index)}], retry_config=kwargs.pop("retry_config", NO_RETRY), **kwargs)


@pytest.mark.parametrize("round_index", [1, 2])
def test_application_32_workers_512_jobs(tmp_path, monkeypatch, round_index):
    monkeypatch.setenv("VIDEO_ORGANIZER_ANALYSIS_LOG_DIR", str(tmp_path / "analysis-logs"))
    prior_handlers = set(logging.getLogger("VideoOrganizer").handlers)
    with measure(f"application-32x512-round-{round_index}") as result:
        with local_server(delay=0.05) as (url, stats):
            settings = SettingsManager.deep_copy_defaults()
            settings["api"].update(key="local-stress-only", base_url=url)
            settings["processing"].update(max_workers=32, extract_parallel=4,
                analysis_retry={"call_extra_attempts": 0, "item_max_attempts": 1, "batch_rerun_enabled": False})
            service = VideoOrganizerService(settings=settings, db_path=str(tmp_path / "stress.db"), on_log=lambda *_: None)
            clients = count_real_clients(monkeypatch, service.ai.gateway)
            paths = []
            for i in range(512):
                path = tmp_path / f"{i}.mp4"
                path.write_bytes(f"synthetic-only-{i}".encode())
                paths.append(str(path))
            service.db.insert_videos_if_absent([{"path": p, "filename": Path(p).name, "status": "pending"} for p in paths])
            monkeypatch.setattr(service.processor, "extract_frames", lambda *_a, **_kw: {"frames": ["synthetic"], "thumbnail": None, "phash": ""})
            monkeypatch.setattr(service.ai, "apply_tag_normalization", lambda data, **_: data)
            monkeypatch.setattr(service.tag_processor, "process", lambda tags, category: (tags, category))
            try:
                outcome = service.run_analysis(paths, force_reanalyze=True)
                statuses = service.db.get_video_statuses_by_paths(paths)
                result.update(requests=stats["requests"], peak_in_flight=stats["peak"], clients_created_during_analysis=len(clients),
                              outcome={key: outcome[key] for key in ("attempted", "succeeded", "failed", "cancelled")})
                assert outcome["succeeded"] == 512
                assert outcome["failed"] == 0
                assert stats["requests"] == 512
                assert 1 < stats["peak"] <= 32
                assert len(statuses) == 512
                assert all(row["status"] == "analyzed" for row in statuses)
                assert len(clients) == 1
            finally:
                for client in set(clients + [service.ai.client]):
                    client.close()
                root = logging.getLogger("VideoOrganizer")
                for handler in set(root.handlers) - prior_handlers:
                    root.removeHandler(handler)
                    handler.close()


@pytest.mark.parametrize("workers", [64, 128, 256])
def test_gateway_super_concurrency_512_requests(monkeypatch, workers):
    with measure(f"gateway-{workers}x512") as result:
        with local_server(barrier=workers, delay=0.05) as (url, stats):
            gateway = AiRequestGateway(gateway_settings(url), timeout=35)
            clients = count_real_clients(monkeypatch, gateway)
            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(request, gateway, i) for i in range(512)]
                    responses = [future.result(timeout=60) for future in futures]
                result.update(requests=stats["requests"], peak_in_flight=stats["peak"], clients_created=len(clients), succeeded=sum(item is not None for item in responses), unique_requests=len(stats["attempts"]))
                assert all(item is not None for item in responses)
                assert stats["requests"] == len(stats["attempts"]) == 512
                assert stats["peak"] == workers
                assert len(clients) == 1
            finally:
                for client in clients:
                    client.close()


@pytest.mark.parametrize("status", [429, 500])
def test_gateway_faults_preserve_retry_policy(monkeypatch, status):
    with measure(f"gateway-fault-{status}-64x128") as result:
        with local_server(delay=0.01, fault=lambda attempt: status if attempt == 1 else 200) as (url, stats):
            gateway = AiRequestGateway(gateway_settings(url), timeout=5)
            clients = count_real_clients(monkeypatch, gateway)
            retry = RetryConfig(call_extra_attempts=1, backoff_base_sec=0.01, backoff_cap_sec=0.01)
            try:
                with ThreadPoolExecutor(max_workers=64) as executor:
                    futures = [executor.submit(request, gateway, i, retry_config=retry) for i in range(128)]
                    responses = [future.result(timeout=15) for future in futures]
                successes = sum(item is not None for item in responses)
                result.update(requests=stats["requests"], succeeded=successes, peak_in_flight=stats["peak"], statuses=dict(stats["statuses"]))
                assert successes == 128
                assert stats["requests"] == 256
                assert len(stats["attempts"]) == 128
                assert all(attempts == 2 for attempts in stats["attempts"].values())
                assert len(clients) == 1
            finally:
                for client in clients:
                    client.close()


def test_gateway_short_timeout_bounded(monkeypatch):
    with measure("gateway-short-timeout-32") as result:
        with local_server(delay=0.4) as (url, stats):
            gateway = AiRequestGateway(gateway_settings(url), timeout=0.1)
            clients = count_real_clients(monkeypatch, gateway)
            try:
                with ThreadPoolExecutor(max_workers=32) as executor:
                    futures = [executor.submit(request, gateway, i) for i in range(32)]
                    responses = [future.result(timeout=10) for future in futures]
                result.update(requests=stats["requests"], succeeded=sum(item is not None for item in responses), peak_in_flight=stats["peak"])
                assert all(item is None for item in responses)
                assert stats["requests"] <= 32
            finally:
                for client in clients:
                    client.close()


def test_cancel_stops_queued_network_requests(monkeypatch):
    held = threading.Event()
    cancel = threading.Event()
    with measure("gateway-cancel-32-workers-256-queued") as result:
        with local_server(barrier=32, delay=0.01, held=held) as (url, stats):
            gateway = AiRequestGateway(gateway_settings(url), timeout=7)
            clients = count_real_clients(monkeypatch, gateway)
            try:
                with ThreadPoolExecutor(max_workers=32) as executor:
                    futures = [executor.submit(request, gateway, i, cancel_event=cancel) for i in range(256)]
                    deadline = time.monotonic() + 10
                    while stats["requests"] < 32 and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert stats["requests"] == 32
                    cancel.set()
                    held.set()
                    responses = [future.result(timeout=15) for future in futures]
                result.update(requests=stats["requests"], succeeded=sum(item is not None for item in responses), cancelled=sum(item is None for item in responses), peak_in_flight=stats["peak"])
                assert stats["requests"] == 32
                assert sum(item is None for item in responses) == 224
                assert len(clients) == 1
            finally:
                held.set()
                for client in clients:
                    client.close()
