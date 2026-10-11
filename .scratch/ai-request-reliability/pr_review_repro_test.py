"""PR #5 只读评审的最小复现；仅使用 fake client 和 pytest 临时目录。"""
import ast
import logging
from pathlib import Path
import os
import subprocess
import threading
from types import SimpleNamespace

from core.ai_gateway import AiRequestGateway
from core.analysis_job_policy import RetryConfig, is_retriable
from core.video_organizer_service import AudioTranscriber


class AuthError(Exception):
    status_code = 401


class FailingChatClient:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        raise AuthError("Error code: 401 - authentication upstream timeout")


def test_auth_status_must_override_transient_message():
    client = FailingChatClient()
    gateway = AiRequestGateway({})
    assert is_retriable(AuthError("Error code: 401 - authentication upstream timeout")) is False
    result = gateway.request_chat(
        model="fake-model",
        system_prompt="fake",
        content_parts=[],
        client=client,
        retry_config=RetryConfig(call_extra_attempts=2),
        sleeper=lambda _: None,
    )
    assert result is None
    assert client.calls == 1, f"401 error produced {client.calls} API calls"
    assert gateway.get_last_failure().retriable is False


def test_failed_ffmpeg_probe_must_preserve_existing_audio(tmp_path, monkeypatch):
    video = tmp_path / "existing.mp4"
    audio = tmp_path / "existing.mp4.mp3"
    video.write_bytes(b"fake video")
    audio.write_bytes(b"pre-existing user audio")

    def missing_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg executable is unavailable")

    monkeypatch.setattr("core.video_organizer_service.subprocess.run", missing_ffmpeg)
    result = AudioTranscriber.transcribe(str(video), gateway=AiRequestGateway({}))
    assert result.startswith("转录失败")
    assert audio.exists(), "An existing file was deleted before audio extraction started"
    assert audio.read_bytes() == b"pre-existing user audio"


def test_missing_audio_must_not_retry_even_with_timeout_in_filename(tmp_path):
    metrics = []
    gateway = AiRequestGateway({}, metrics_recorder=lambda **kwargs: metrics.append(kwargs))
    result = gateway.transcribe_audio(
        str(tmp_path / "timeout.mp3"),
        client=SimpleNamespace(),
        retry_config=RetryConfig(call_extra_attempts=2),
        sleeper=lambda _: None,
    )
    assert result is None
    assert len(metrics) == 1, f"FileNotFoundError produced {len(metrics)} attempts"
    assert gateway.get_last_failure().retriable is False


def baseline_ast():
    root = Path(__file__).resolve().parents[2]
    source = subprocess.check_output(
        ["git", "show", "58b6dc2d5286048216bcacb4c5d56796d4026821:core/video_organizer_service.py"],
        cwd=root,
        encoding="utf-8",
    )
    return ast.parse(source)


def test_base_revision_did_not_retry_auth_error():
    tree = baseline_ast()
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AIHandler")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_get_api_response")
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    isolated = ast.fix_missing_locations(ast.Module(body=[future, method], type_ignores=[]))
    namespace = {"logger": logging.getLogger("pr_review_baseline")}
    exec(compile(isolated, "baseline_AIHandler", "exec"), namespace)
    client = FailingChatClient()
    handler = SimpleNamespace(
        settings={},
        client=client,
        _job_retry_config=RetryConfig(call_extra_attempts=2),
        _retry_sleeper=lambda _: None,
        _tls=threading.local(),
        _record_api_metrics=lambda **_: None,
    )
    result = namespace["_get_api_response"](handler, "fake-model", "fake", [])
    assert result is None
    assert client.calls == 1
    assert handler._last_api_failure.retriable is False


def test_base_revision_preserved_audio_when_ffmpeg_probe_failed(tmp_path):
    tree = baseline_ast()
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AudioTranscriber")
    isolated = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))

    def missing_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg executable is unavailable")

    namespace = {
        "OpenAI": lambda **kwargs: SimpleNamespace(),
        "subprocess": SimpleNamespace(run=missing_ffmpeg),
        "os": os,
    }
    exec(compile(isolated, "baseline_AudioTranscriber", "exec"), namespace)
    video = tmp_path / "existing.mp4"
    audio = tmp_path / "existing.mp4.mp3"
    audio.write_bytes(b"pre-existing user audio")
    result = namespace["AudioTranscriber"].transcribe(str(video), "fake-key", "https://example.invalid/v1")
    assert result.startswith("转录失败")
    assert audio.read_bytes() == b"pre-existing user audio"


def configure_route_switch(monkeypatch):
    from core.model_providers import make_provider, set_task_route

    settings = {
        "model_providers": [
            make_provider(provider_id="a", api_key="fake-a", base_url="https://a.example.invalid/v1"),
            make_provider(provider_id="b", api_key="fake-b", base_url="https://b.example.invalid/v1"),
        ],
        "current_provider_id": "a",
    }
    set_task_route(settings, "video_classification", provider_id="a", model="model-a")
    gateway = AiRequestGateway(settings)
    resolver = gateway.resolve_route
    switched = False
    calls = []

    def resolve_then_switch(task_key):
        nonlocal switched
        route = resolver(task_key)
        if not switched:
            switched = True
            set_task_route(settings, task_key, provider_id="b", model="model-b")
        return route

    def make_client(route, timeout):
        connection = dict(route)

        def create(**kwargs):
            calls.append((connection["provider_id"], kwargs["model"]))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(gateway, "resolve_route", resolve_then_switch)
    monkeypatch.setattr(gateway, "_client_for_route", make_client)
    return settings, gateway, calls


def test_chat_route_switch_must_keep_model_and_connection_together(monkeypatch):
    settings, gateway, calls = configure_route_switch(monkeypatch)
    result = gateway.request_chat(
        model="", system_prompt="fake", content_parts=[], task_key="video_classification"
    )
    assert result == {"ok": True}
    assert calls in [[("a", "model-a")], [("b", "model-b")]], f"Mixed route snapshot: {calls}"


def test_base_revision_kept_route_model_and_connection_together(monkeypatch):
    settings, gateway, calls = configure_route_switch(monkeypatch)
    tree = baseline_ast()
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AIHandler")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_get_api_response")
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    isolated = ast.fix_missing_locations(ast.Module(body=[future, method], type_ignores=[]))
    namespace = {"logger": logging.getLogger("pr_review_baseline"), "json": __import__("json")}
    exec(compile(isolated, "baseline_AIHandler", "exec"), namespace)
    handler = SimpleNamespace(
        settings=settings,
        client_for_task=gateway.client_for_task,
        _job_retry_config=RetryConfig(call_extra_attempts=2),
        _retry_sleeper=lambda _: None,
        _tls=threading.local(),
        _record_api_metrics=lambda **_: None,
    )
    result = namespace["_get_api_response"](
        handler, "", "fake", [], task_key="video_classification"
    )
    assert result == {"ok": True}
    assert calls == [("a", "model-a")]
