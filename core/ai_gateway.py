# -*- coding: utf-8 -*-
"""AI 请求传输网关。

业务代码只通过本模块访问 OpenAI 兼容聊天和音频转录接口。供应商路由、
客户端缓存、调用层重试、取消检查和响应解析集中在这里，避免每个业务入口
各自实现一套容易漂移的请求逻辑。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI

from core.analysis_job_policy import (
    ApiCallFailure,
    RetryConfig,
    is_retriable,
    should_retry_call,
    sleep_backoff,
)
from core.model_providers import list_providers, resolve_current_provider, resolve_task_route


logger = logging.getLogger("VideoOrganizer.AI")
DEFAULT_AI_TIMEOUT_SECONDS = 45.0
EMPTY_API_KEY = "EMPTY"


class AiResponseError(ValueError):
    """AI 返回无法消费的响应。"""


def _status_code(error: Any) -> Optional[int]:
    """读取 OpenAI SDK、HTTP 客户端或测试 fake 暴露的状态码。"""
    code = getattr(error, "status_code", None)
    if code is None:
        response = getattr(error, "response", None)
        code = getattr(response, "status_code", None) if response is not None else None
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def _error_message(error: BaseException) -> str:
    message = str(error) or error.__class__.__name__
    return message[:200] + ("…" if len(message) > 200 else "")


def _content_to_text(content: Any) -> str:
    """兼容字符串、字典和 OpenAI 新版 content part 列表。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            value = content.get(key)
            if isinstance(value, (str, bytes)):
                return _content_to_text(value)
        return ""
    if isinstance(content, (list, tuple)):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
            else:
                text = getattr(part, "text", None) or getattr(part, "content", None)
            if text is not None:
                parts.append(_content_to_text(text))
        return "".join(parts)
    return str(content)


def _message_content(response: Any) -> Any:
    choices = getattr(response, "choices", None)
    if not choices:
        raise AiResponseError("empty choices")
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    if message is None:
        raise AiResponseError("empty message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if content is None:
        refusal = message.get("refusal") if isinstance(message, dict) else getattr(message, "refusal", None)
        if refusal:
            raise AiResponseError(f"模型拒绝返回内容: {_content_to_text(refusal)}")
        raise AiResponseError("empty body")
    text = _content_to_text(content)
    if not text.strip():
        raise AiResponseError("empty body")
    return text


def _parse_json_content(content: Any) -> Any:
    if isinstance(content, (dict, list)):
        return content
    text = _content_to_text(content).strip()
    if not text:
        raise AiResponseError("empty body")
    # 兼容少数服务仍返回 Markdown JSON 围栏；业务层仍会做结构校验。
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AiResponseError(f"invalid JSON: {exc}") from exc


def _response_format_unsupported(error: BaseException) -> bool:
    """判断是否应对不支持 response_format 的兼容服务做一次降级重发。"""
    if _status_code(error) not in (400, 404, 422):
        return False
    text = str(error).lower()
    mentions_format = any(
        marker in text
        for marker in ("response_format", "json_object", "json mode", "json-mode")
    )
    unsupported = any(
        marker in text
        for marker in ("unsupported", "not support", "unknown parameter", "invalid parameter", "不支持")
    )
    return mentions_format and unsupported


class AiRequestGateway:
    """所有活动 AI 请求共用的传输层。"""

    def __init__(
        self,
        settings: Dict,
        *,
        metrics_recorder: Optional[Callable[..., None]] = None,
        timeout: float = DEFAULT_AI_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings
        self.timeout = float(timeout)
        self.metrics_recorder = metrics_recorder
        self._clients: Dict[Tuple[str, str, str], Any] = {}
        self._default_client: Any = None
        self._default_fingerprint: Optional[Tuple[str, str, str]] = None
        self._tls = threading.local()
        self._last_failure: Optional[ApiCallFailure] = None

    @staticmethod
    def _connection_fingerprint(route: Dict[str, Any]) -> Tuple[str, str, str]:
        return (
            str(route.get("provider_id") or "_default"),
            str(route.get("base_url") or "").strip(),
            str(route.get("api_key") or ""),
        )

    @staticmethod
    def _client_for_route(route: Dict[str, Any], timeout: float) -> Any:
        return OpenAI(
            api_key=str(route.get("api_key") or EMPTY_API_KEY),
            base_url=str(route.get("base_url") or "").strip() or None,
            max_retries=0,
            timeout=timeout,
        )

    def _current_route(self) -> Dict[str, Any]:
        providers = list_providers(self.settings)
        if providers:
            provider = resolve_current_provider(self.settings)
        else:
            # 兼容尚未迁移的内存配置，不为构造默认客户端额外改写 settings。
            api = self.settings.get("api") if isinstance(self.settings, dict) else {}
            api = api if isinstance(api, dict) else {}
            provider = {
                "id": "_default",
                "display_name": "默认",
                "api_key": api.get("key") or "",
                "base_url": api.get("base_url") or "",
            }
        return {
            "task_key": "_current_provider",
            "provider_id": provider.get("id") or "_default",
            "display_name": provider.get("display_name") or "",
            "api_key": provider.get("api_key") or "",
            "base_url": provider.get("base_url") or "",
            "model": "",
        }

    def resolve_route(self, task_key: str) -> Dict[str, Any]:
        return resolve_task_route(self.settings, task_key)

    def default_client(self) -> Any:
        """返回当前供应商的兼容客户端，供旧门面和测试注入使用。"""
        route = self._current_route()
        fingerprint = self._connection_fingerprint(route)
        if self._default_client is None or self._default_fingerprint != fingerprint:
            self._default_client = self._client_for_route(route, self.timeout)
            self._default_fingerprint = fingerprint
        return self._default_client

    @property
    def client_cache(self) -> Dict[Tuple[str, str, str], Any]:
        """只读访问兼容门面使用的客户端缓存映射。"""
        return self._clients

    def client_for_task(self, task_key: str) -> Tuple[Any, Dict[str, Any]]:
        route = self.resolve_route(task_key)
        fingerprint = self._connection_fingerprint(route)
        # 同一供应商凭据被修改后丢弃旧缓存；已在进行中的调用仍持有自己的 client 引用。
        for old_fingerprint in list(self._clients):
            if old_fingerprint[0] == fingerprint[0] and old_fingerprint != fingerprint:
                self._clients.pop(old_fingerprint, None)
        client = self._clients.get(fingerprint)
        if client is None:
            client = self._client_for_route(route, self.timeout)
            self._clients[fingerprint] = client
        return client, route

    def reload(self) -> Any:
        """清空所有连接缓存，并返回最新当前供应商客户端。"""
        self._clients.clear()
        self._default_client = None
        self._default_fingerprint = None
        return self.default_client()

    def _set_failure(self, failure: Optional[ApiCallFailure]) -> None:
        self._last_failure = failure
        self._tls.last_failure = failure

    def get_last_failure(self) -> Optional[ApiCallFailure]:
        failure = getattr(self._tls, "last_failure", None)
        return failure if failure is not None else self._last_failure

    def _record_metrics(
        self,
        *,
        attempt: int,
        model: str,
        task_key: str,
        ok: bool,
        error: str = "",
        retriable: Optional[bool] = None,
    ) -> None:
        if self.metrics_recorder is None:
            return
        try:
            self.metrics_recorder(
                attempt=attempt,
                model=model,
                task_key=task_key,
                ok=ok,
                error=error,
                retriable=retriable,
            )
        except Exception:
            logger.debug("AI 指标记录失败", exc_info=True)

    @staticmethod
    def _cancelled(cancel_event: Any) -> bool:
        try:
            return bool(cancel_event is not None and cancel_event.is_set())
        except Exception:
            return False

    def _failure_and_return(self, message: str, *, retriable: bool, cancelled: bool = False) -> None:
        self._set_failure(
            ApiCallFailure(message=message, retriable=retriable, cancelled=cancelled)
        )

    def request_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        content_parts: List[Any],
        json_mode: bool = True,
        task_key: Optional[str] = None,
        client: Any = None,
        retry_config: Optional[RetryConfig] = None,
        cancel_event: Any = None,
        sleeper: Optional[Callable[[float], None]] = None,
    ) -> Any:
        """请求聊天补全，返回 JSON 对象或文本；失败统一返回 None。"""
        use_client = client
        use_model = model
        if task_key:
            # 模型与连接来自同一份路由快照，避免设置切换时混用两次解析结果。
            if use_client is None:
                use_client, route = self.client_for_task(task_key)
            else:
                route = self.resolve_route(task_key)
            # 任务路由中的模型是唯一真相；显式 client 只用于测试或特殊适配器。
            use_model = str(route.get("model") or model or "gemini-2.0-flash")
        if use_client is None:
            use_client = self.default_client()

        cfg = retry_config or RetryConfig.from_settings(self.settings)
        self._set_failure(None)
        attempt = 0
        compatibility_fallback_used = False
        include_response_format = bool(json_mode)

        while True:
            if self._cancelled(cancel_event):
                logger.info("AI 调用因用户取消而中止")
                self._failure_and_return("用户取消", retriable=False, cancelled=True)
                return None

            attempt += 1
            logger.info(
                "正在调用 AI 模型: %s (任务: %s, JSON 模式: %s, 尝试 %s)",
                use_model,
                task_key or "兼容调用",
                json_mode,
                attempt,
            )
            kwargs: Dict[str, Any] = {
                "model": use_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content_parts},
                ],
                "temperature": 0.2,
            }
            if include_response_format:
                kwargs["response_format"] = {"type": "json_object"}

            try:
                response = use_client.chat.completions.create(**kwargs)
                content = _message_content(response)
                result = _parse_json_content(content) if json_mode else content
                self._set_failure(None)
                self._record_metrics(
                    attempt=attempt,
                    model=str(use_model or ""),
                    task_key=task_key or "",
                    ok=True,
                )
                return result
            except Exception as error:
                message = _error_message(error)
                if (
                    json_mode
                    and include_response_format
                    and not compatibility_fallback_used
                    and _response_format_unsupported(error)
                ):
                    compatibility_fallback_used = True
                    include_response_format = False
                    logger.warning(
                        "AI 服务不支持 response_format，改用客户端 JSON 解析重试"
                    )
                    self._record_metrics(
                        attempt=attempt,
                        model=str(use_model or ""),
                        task_key=task_key or "",
                        ok=False,
                        error=message,
                        retriable=False,
                    )
                    continue

                retriable = is_retriable(error)
                logger.error("AI API 调用出错: %s", message)
                self._record_metrics(
                    attempt=attempt,
                    model=str(use_model or ""),
                    task_key=task_key or "",
                    ok=False,
                    error=message,
                    retriable=retriable,
                )
                if not retriable:
                    self._failure_and_return(message, retriable=False)
                    return None
                if not should_retry_call(
                    attempt, call_extra_attempts=cfg.call_extra_attempts
                ):
                    self._failure_and_return(message, retriable=True)
                    return None
                sleep_backoff(attempt, config=cfg, sleeper=sleeper)

    def transcribe_audio(
        self,
        audio_path: str,
        *,
        task_key: str = "content_description",
        model: str = "whisper-1",
        client: Any = None,
        retry_config: Optional[RetryConfig] = None,
        cancel_event: Any = None,
        sleeper: Optional[Callable[[float], None]] = None,
    ) -> Optional[str]:
        """请求音频转录，使用与聊天请求相同的连接和调用层策略。"""
        use_client = client
        if task_key and use_client is None:
            use_client, _ = self.client_for_task(task_key)
        if use_client is None:
            use_client = self.default_client()

        cfg = retry_config or RetryConfig.from_settings(self.settings)
        self._set_failure(None)
        attempt = 0
        path = Path(audio_path)
        while True:
            if self._cancelled(cancel_event):
                self._failure_and_return("用户取消", retriable=False, cancelled=True)
                return None
            attempt += 1
            try:
                with path.open("rb") as audio_file:
                    response = use_client.audio.transcriptions.create(
                        model=model,
                        file=audio_file,
                        response_format="text",
                    )
                text = _content_to_text(response).strip()
                if not text:
                    raise AiResponseError("empty transcription")
                self._set_failure(None)
                self._record_metrics(
                    attempt=attempt,
                    model=model,
                    task_key=task_key,
                    ok=True,
                )
                return text
            except Exception as error:
                message = _error_message(error)
                retriable = is_retriable(error)
                self._record_metrics(
                    attempt=attempt,
                    model=model,
                    task_key=task_key,
                    ok=False,
                    error=message,
                    retriable=retriable,
                )
                if not retriable:
                    self._failure_and_return(message, retriable=False)
                    return None
                if not should_retry_call(
                    attempt, call_extra_attempts=cfg.call_extra_attempts
                ):
                    self._failure_and_return(message, retriable=True)
                    return None
                sleep_backoff(attempt, config=cfg, sleeper=sleeper)
