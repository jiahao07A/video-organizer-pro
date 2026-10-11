"""统一 AI 请求网关回归测试。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from core.ai_gateway import AiRequestGateway
from core.analysis_job_policy import RetryConfig
from core.tag_import_service import TagImportService


class FakeChatClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


class UnsupportedResponseFormatError(Exception):
    status_code = 400


def fake_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_chat_retries_and_parses_structured_content():
    client = FakeChatClient(
        [
            TimeoutError("timed out"),
            fake_response(
                [
                    {"type": "text", "text": '{"ok": '},
                    {"type": "text", "text": "true}"},
                ]
            ),
        ]
    )
    gateway = AiRequestGateway({})
    result = gateway.request_chat(
        model="m",
        system_prompt="sys",
        content_parts=[{"type": "text", "text": "user"}],
        client=client,
        retry_config=RetryConfig(call_extra_attempts=1),
        sleeper=lambda _seconds: None,
    )
    assert result == {"ok": True}
    assert len(client.calls) == 2
    assert "response_format" in client.calls[0]


def test_chat_falls_back_when_response_format_is_unsupported():
    error = UnsupportedResponseFormatError(
        "400 unsupported parameter response_format json_object"
    )
    client = FakeChatClient([error, fake_response("```json\n{\"ok\": true}\n```")])
    gateway = AiRequestGateway({})

    result = gateway.request_chat(
        model="m",
        system_prompt="sys",
        content_parts=[],
        client=client,
        retry_config=RetryConfig(call_extra_attempts=0),
    )

    assert result == {"ok": True}
    assert len(client.calls) == 2
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]


def test_auth_error_is_not_retried():
    class AuthError(Exception):
        status_code = 401

    client = FakeChatClient([AuthError("401 Unauthorized")])
    gateway = AiRequestGateway({})

    result = gateway.request_chat(
        model="m",
        system_prompt="sys",
        content_parts=[],
        client=client,
        retry_config=RetryConfig(call_extra_attempts=5),
    )

    assert result is None
    assert len(client.calls) == 1
    assert gateway.get_last_failure().retriable is False


def test_audio_transcription_uses_gateway_retry(tmp_path: Path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")

    class AudioClient:
        def __init__(self):
            self.calls = 0
            self.audio = SimpleNamespace(
                transcriptions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            self.calls += 1
            assert kwargs["model"] == "whisper-1"
            assert kwargs["file"].read() == b"audio"
            if self.calls == 1:
                raise TimeoutError("timed out")
            return "hello"

    client = AudioClient()
    gateway = AiRequestGateway({})
    result = gateway.transcribe_audio(
        str(audio),
        client=client,
        retry_config=RetryConfig(call_extra_attempts=1),
        sleeper=lambda _seconds: None,
    )

    assert result == "hello"
    assert client.calls == 2


def test_tag_import_uses_tag_generation_route():
    class FakeAi:
        tag_config = {
            "tag_groups": [
                {"id": "mood", "name": "氛围", "tags": [], "rules": {}}
            ]
        }
        settings = {}

        def __init__(self):
            self.task_keys = []

        def _get_api_response(self, **kwargs):
            self.task_keys.append(kwargs.get("task_key"))
            return {"开心": "mood"}

    ai = FakeAi()
    result = TagImportService(ai).classify_tags_with_ai(["开心"])
    assert result == {"mood": ["开心"]}
    assert ai.task_keys == ["tag_generation"]


def test_client_cache_key_changes_when_provider_credentials_change(monkeypatch):
    from core.model_providers import ensure_providers, update_provider
    from core.video_organizer_service import SettingsManager

    settings = SettingsManager.deep_copy_defaults()
    ensure_providers(settings)
    provider_id = settings["current_provider_id"]
    created = []

    def make_client(**kwargs):
        client = SimpleNamespace(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr("core.ai_gateway.OpenAI", make_client)
    gateway = AiRequestGateway(settings)
    first, _ = gateway.client_for_task("video_classification")
    update_provider(settings, provider_id, api_key="changed-key")
    second, _ = gateway.client_for_task("video_classification")

    assert first is not second
    assert len(created) == 2
    assert len(gateway._clients) == 1
    assert second.api_key == "changed-key"


def test_empty_choices_is_retried_as_transient_response():
    client = FakeChatClient(
        [
            SimpleNamespace(choices=[]),
            fake_response('{"ok": true}'),
        ]
    )
    gateway = AiRequestGateway({})

    result = gateway.request_chat(
        model="m",
        system_prompt="sys",
        content_parts=[],
        client=client,
        retry_config=RetryConfig(call_extra_attempts=1),
        sleeper=lambda _seconds: None,
    )

    assert result == {"ok": True}
    assert len(client.calls) == 2


def test_cancelled_request_does_not_submit_call():
    client = FakeChatClient([fake_response('{"ok": true}')])
    cancel = threading.Event()
    cancel.set()
    gateway = AiRequestGateway({})

    result = gateway.request_chat(
        model="m",
        system_prompt="sys",
        content_parts=[],
        client=client,
        cancel_event=cancel,
    )

    assert result is None
    assert client.calls == []
    failure = gateway.get_last_failure()
    assert failure.cancelled is True
    assert failure.retriable is False


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
@pytest.mark.parametrize("request_kind", ["chat", "audio"])
def test_http_error_status_overrides_transient_text(tmp_path, status_code, request_kind):
    class HttpError(Exception):
        def __init__(self):
            super().__init__("upstream timeout: invalid JSON, rate limit")
            self.status_code = status_code

    error = HttpError()
    client = FakeChatClient([error, error, error])
    audio_calls = []

    def transcribe(**kwargs):
        audio_calls.append(kwargs)
        raise error

    client.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe))
    metrics = []
    gateway = AiRequestGateway({}, metrics_recorder=lambda **entry: metrics.append(entry))
    options = {
        "client": client,
        "retry_config": RetryConfig(call_extra_attempts=2),
        "sleeper": lambda _: None,
    }
    if request_kind == "chat":
        result = gateway.request_chat(model="m", system_prompt="sys", content_parts=[], **options)
        calls = client.calls
    else:
        audio = tmp_path / "voice.mp3"
        audio.write_bytes(b"fake audio")
        result = gateway.transcribe_audio(str(audio), **options)
        calls = audio_calls

    assert result is None
    assert len(calls) == 1
    assert len(metrics) == 1
    assert gateway.get_last_failure().retriable is False


def test_missing_audio_with_timeout_in_filename_is_not_retried(tmp_path):
    metrics = []
    gateway = AiRequestGateway({}, metrics_recorder=lambda **entry: metrics.append(entry))
    result = gateway.transcribe_audio(
        str(tmp_path / "timeout.mp3"),
        client=SimpleNamespace(),
        retry_config=RetryConfig(call_extra_attempts=2),
        sleeper=lambda _: None,
    )

    assert result is None
    assert len(metrics) == 1
    assert gateway.get_last_failure().retriable is False


def test_route_switch_keeps_connection_and_model_in_one_snapshot(monkeypatch):
    from core.model_providers import make_provider, resolve_task_route, set_task_route

    settings = {
        "model_providers": [
            make_provider(provider_id="a", api_key="fake-a", base_url="https://a.example.invalid/v1"),
            make_provider(provider_id="b", api_key="fake-b", base_url="https://b.example.invalid/v1"),
        ],
        "current_provider_id": "a",
    }
    set_task_route(settings, "video_classification", provider_id="a", model="model-a")
    switched = False
    calls = []

    def resolve_then_switch(current_settings, task_key):
        nonlocal switched
        route = resolve_task_route(current_settings, task_key)
        if not switched:
            switched = True
            set_task_route(current_settings, task_key, provider_id="b", model="model-b")
        return route

    def make_client(**connection):
        def create(**kwargs):
            calls.append((connection["api_key"], kwargs["model"]))
            return fake_response('{"ok": true}')

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr("core.ai_gateway.resolve_task_route", resolve_then_switch)
    monkeypatch.setattr("core.ai_gateway.OpenAI", make_client)
    gateway = AiRequestGateway(settings)

    assert gateway.request_chat(model="", system_prompt="sys", content_parts=[], task_key="video_classification") == {"ok": True}
    assert gateway.request_chat(model="", system_prompt="sys", content_parts=[], task_key="video_classification") == {"ok": True}
    assert calls == [("fake-a", "model-a"), ("fake-b", "model-b")]


def test_reload_preserves_in_flight_client_and_updates_new_requests(monkeypatch):
    from core.model_providers import make_provider, set_task_route, update_provider

    settings = {
        "model_providers": [make_provider(provider_id="a", api_key="fake-old", base_url="https://example.invalid/v1")],
        "current_provider_id": "a",
    }
    set_task_route(settings, "video_classification", provider_id="a", model="m")
    started = threading.Event()
    release = threading.Event()
    clients = []

    def make_client(**connection):
        client = SimpleNamespace(closed=False)
        clients.append(client)

        def create(**kwargs):
            if connection["api_key"] == "fake-old":
                started.set()
                assert release.wait(timeout=5)
            assert not client.closed
            return fake_response('{"key": "' + connection["api_key"] + '"}')

        client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        client.close = lambda: setattr(client, "closed", True)
        return client

    monkeypatch.setattr("core.ai_gateway.OpenAI", make_client)
    gateway = AiRequestGateway(settings)
    options = {"model": "", "system_prompt": "sys", "content_parts": [], "task_key": "video_classification"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(gateway.request_chat, **options)
        try:
            assert started.wait(timeout=5)
            update_provider(settings, "a", api_key="fake-new")
            gateway.reload()
            assert gateway.request_chat(**options) == {"key": "fake-new"}
        finally:
            release.set()
        assert running.result(timeout=5) == {"key": "fake-old"}

    assert all(not client.closed for client in clients)


def test_explicit_chat_client_resolves_route_once(monkeypatch):
    from core.model_providers import resolve_task_route

    resolutions = []

    def counted_route(settings, task_key):
        resolutions.append(task_key)
        return resolve_task_route(settings, task_key)

    monkeypatch.setattr("core.ai_gateway.resolve_task_route", counted_route)
    gateway = AiRequestGateway({})
    client = FakeChatClient([fake_response('{"ok": true}')])
    result = gateway.request_chat(
        model="", system_prompt="sys", content_parts=[], task_key="video_classification", client=client
    )

    assert result == {"ok": True}
    assert resolutions == ["video_classification"]


def test_audio_connection_resolves_route_once(tmp_path, monkeypatch):
    from core.model_providers import resolve_task_route

    resolutions = []

    def counted_route(settings, task_key):
        resolutions.append(task_key)
        return resolve_task_route(settings, task_key)

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **_: "transcript")))
    monkeypatch.setattr("core.ai_gateway.resolve_task_route", counted_route)
    monkeypatch.setattr("core.ai_gateway.OpenAI", lambda **_: client)
    gateway = AiRequestGateway({})
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake audio")

    assert gateway.transcribe_audio(str(audio)) == "transcript"
    assert resolutions == ["content_description"]
