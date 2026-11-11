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
import time
import uuid
from core.diagnostic_logging import event, anonymous, register_settings
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
    if _status_code(error) == 400 and any(
        text in message.lower()
        for text in ("the content of image_url field is unsafe", "the content of image_url.url field is unsafe")
    ):
        # 明确这是服务端对本次图像输入的拒绝，不能推断素材损坏或客户端版本回归。
        # 是否重试由视频恢复上下文决定，不从拒绝消息推断素材损坏。
        return (
            "AI 服务拒绝了本次图像输入（HTTP 400，image_url unsafe）。"
            "不能据此判定素材损坏；是否重试取决于400恢复设置。请核查服务端图像审核/接口限制。"
        )
    if type(error).__module__.split('.')[0] in {'openai', 'httpx', 'httpcore', 'requests'}:
        return f"AI 接口错误（HTTP {_status_code(error) or 'unavailable'}，{type(error).__name__}）"
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
            raise AiResponseError("模型拒绝返回内容（response_refused）")
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
        register_settings(settings)
        self.timeout = float(timeout)
        self.metrics_recorder = metrics_recorder
        self._clients: Dict[Tuple[str, str, str], Any] = {}
        self._client_lock = threading.RLock()
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
        with self._client_lock:
            if self._default_client is None or self._default_fingerprint != fingerprint:
                self._default_client = self._clients.get(fingerprint)
                if self._default_client is None:
                    self._default_client = self._client_for_route(route, self.timeout)
                    self._clients[fingerprint] = self._default_client
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
        # 只保护客户端创建/缓存；网络请求在锁外，不能把32路请求串行化。
        with self._client_lock:
            for old_fingerprint in list(self._clients):
                if old_fingerprint[0] == fingerprint[0] and old_fingerprint != fingerprint:
                    self._clients.pop(old_fingerprint, None)
            client = self._clients.get(fingerprint)
            if client is None:
                client = self._client_for_route(route, self.timeout)
                self._clients[fingerprint] = client
        return client, route

    def test_connection(self, task_key: str = "video_classification") -> Dict[str, Any]:
        """调用供应商模型列表接口验证路由连接，不返回凭据。"""
        client, route = self.client_for_task(task_key)
        try:
            models_api = getattr(client, "models", None)
            list_models = getattr(models_api, "list", None)
            if not callable(list_models):
                raise RuntimeError("当前客户端不支持模型列表探测")
            response = list_models()
        except Exception as error:
            code = _status_code(error)
            if code in (401, 403):
                raise RuntimeError("鉴权失败，请检查 API Key") from error
            if code == 404:
                raise RuntimeError("接口地址不存在，请检查 API Base URL") from error
            text = _error_message(error)
            lowered = text.lower()
            if any(marker in lowered for marker in ("timeout", "timed out", "connection", "connect")):
                raise RuntimeError("网络连接失败，请检查网络或 API Base URL") from error
            raise RuntimeError(f"连接失败：{text}") from error

        data = getattr(response, "data", None)
        count = len(data) if isinstance(data, (list, tuple)) else None
        return {
            "task_key": task_key,
            "provider_id": route.get("provider_id") or "",
            "display_name": route.get("display_name") or "",
            "model": route.get("model") or "",
            "model_count": count,
        }

    def reload(self) -> Any:
        """清空所有连接缓存，并返回最新当前供应商客户端。"""
        with self._client_lock:
            self._clients.clear()
            self._default_client = None
            self._default_fingerprint = None
            return self.default_client()

    def list_models(self, *, route_override: Dict[str, Any]) -> List[str]:
        """读取指定连接的候选模型，不切换供应商或执行推理。"""
        try:
            client = self.client_for_route(route_override)
            response = client.models.list()
        except Exception as error:
            code = _status_code(error)
            if code in (401, 403):
                message = "鉴权失败，请检查该供应商的 API Key；仍可手动填写模型。"
            elif code in (404, 405, 501) or isinstance(error, AttributeError):
                message = "此连接不提供模型列表，请检查 Base URL 或手动填写模型。"
            elif code == 429:
                message = "模型列表请求过于频繁，请稍后重试或手动填写。"
            elif code in (408, 504) or (code is None and any(word in str(error).lower() for word in ("timeout", "timed out", "connection", "connect"))):
                message = "获取模型列表超时或网络连接失败，请稍后重试；仍可手动填写。"
            else:
                message = f"获取模型列表失败（HTTP {code}），可手动填写。" if code else "获取模型列表失败，请检查连接配置；仍可手动填写。"
            # 供应商返回的错误可能包含凭据或请求内容，不回传原文。
            raise RuntimeError(message) from None
        data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
        if not isinstance(data, (list, tuple)):
            raise RuntimeError("模型列表格式无法识别，仍可手动填写模型。")
        names = set()
        for item in data:
            name = item if isinstance(item, str) else item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
        return sorted(names, key=lambda value: (value.casefold(), value))

    def _set_failure(self, failure: Optional[ApiCallFailure]) -> None:
        self._last_failure = failure
        self._tls.last_failure = failure

    def get_last_failure(self) -> Optional[ApiCallFailure]:
        return getattr(self._tls, "last_failure", None)

    def client_for_route(self, route):
        fingerprint = self._connection_fingerprint(route)
        with self._client_lock:
            if fingerprint not in self._clients:
                self._clients[fingerprint] = self._client_for_route(route, self.timeout)
            return self._clients[fingerprint]

    def _client_for_recovery_route(self, route, context):
        try:
            return self.client_for_route(route)
        except Exception as error:
            if context is None or not context.active:
                raise
            # 连接尚未建成，没有发出请求；跳过该候选，不扣预算或重复初始化。
            from core.analysis_recovery import redact
            message = redact(f"API连接初始化失败：{error}", [r.get("api_key") for r in context.routes])
            failure = ValueError(message)
            context.record(failure)
            self._set_failure(ApiCallFailure(
                message=message, retriable=False, task_key=context.task_key,
                provider_id=route.get("provider_id", ""), model=route.get("model", ""),
                recovery_handled=True))
            context.handle_failure(failure, manual=getattr(context, "manual", False))
            return None

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
        route_override: Optional[Dict[str, Any]] = None,
        call_context: Any = None,
    ) -> Any:
        """请求聊天补全，返回 JSON 对象或文本；失败统一返回 None。"""
        use_client = client
        use_model = model
        context = call_context or getattr(self._tls, "recovery_context", None)
        if context is not None and task_key != context.task_key:
            context = None
        route_override = route_override or (context.route if context else None)
        route = route_override or {}
        if route_override:
            use_client = use_client or self._client_for_recovery_route(route_override, context)
            if use_client is None:
                return None
            use_model = route_override.get("model") or model
        elif task_key:
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
        include_response_format = bool(json_mode) and not (context and context.route_index in context.compatibility)

        while True:
            if self._cancelled(cancel_event):
                logger.info("AI 调用因用户取消而中止")
                self._failure_and_return("用户取消", retriable=False, cancelled=True)
                return None

            if context and not context.before_request():
                self._failure_and_return(context.stop_reason, retriable=False)
                self._tls.last_failure.recovery_handled = True
                return None

            attempt += 1
            register_settings(self.settings)
            request_id = uuid.uuid4().hex
            started = time.monotonic()
            request_fields = dict(request_id=request_id, task=task_key, model=use_model, provider=anonymous(route.get('provider_id', '')), attempt=attempt, interface='chat', json_mode=include_response_format, input_parts=len(content_parts), input_chars=sum(len(str(p)) for p in content_parts))
            event('api.request', **request_fields)
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
                event('api.success', **request_fields, elapsed_ms=round((time.monotonic() - started) * 1000), server_request_id=getattr(response, '_request_id', None), usage={k: getattr(getattr(response, 'usage', None), k, None) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')})
                if context:
                    context.record(success=True)
                self._set_failure(None)
                self._record_metrics(
                    attempt=attempt,
                    model=str(use_model or ""),
                    task_key=task_key or "",
                    ok=True,
                )
                return result
            except Exception as error:
                event('api.failed', 40, **request_fields, elapsed_ms=round((time.monotonic() - started) * 1000), status_code=_status_code(error), server_request_id=getattr(error, 'request_id', None), error_type=type(error).__name__, retryable=is_retriable(error))
                logger.debug('api.exception', exc_info=True)
                message = _error_message(error)
                if context:
                    from core.analysis_recovery import redact
                    message = redact(message, [r.get("api_key") for r in context.routes])
                compatibility_error = bool(json_mode and include_response_format and not compatibility_fallback_used and _response_format_unsupported(error))
                if context:
                    context.record(error, compatibility=compatibility_error)
                    self._set_failure(ApiCallFailure(
                        message=message, retriable=False, status_code=_status_code(error),
                        error_code=context.last_error.get("error_code", ""),
                        task_key=task_key or "", provider_id=route.get("provider_id", ""),
                        model=use_model, request_id=str(getattr(error, "request_id", "") or ""),
                        recovery_handled=context.active))
                if (
                    json_mode
                    and include_response_format
                    and not compatibility_fallback_used
                    and _response_format_unsupported(error)
                ):
                    compatibility_fallback_used = True
                    include_response_format = False
                    if context:
                        context.compatibility.add(context.route_index)
                    logger.warning(
                        "AI 服务不支持 response_format，改用客户端 JSON 解析重试"
                    )
                    self._record_metrics(
                        attempt=attempt,
                        model=str(use_model or ""),
                        task_key=task_key or "",
                        ok=False,
                        error=type(error).__name__,
                        retriable=False,
                    )
                    continue

                retriable = is_retriable(error)
                logger.error("AI API 调用出错: %s (HTTP %s)", type(error).__name__, _status_code(error))
                self._record_metrics(
                    attempt=attempt,
                    model=str(use_model or ""),
                    task_key=task_key or "",
                    ok=False,
                    error=type(error).__name__,
                    retriable=retriable,
                )
                if context and context.handle_failure(error, manual=getattr(context, "manual", False)):
                    return None
                if not retriable:
                    self._failure_and_return(message, retriable=False)
                    body = getattr(error, "body", None)
                    self._tls.last_failure.status_code = _status_code(error)
                    self._tls.last_failure.task_key = task_key or ""
                    self._tls.last_failure.provider_id = route.get("provider_id", "")
                    self._tls.last_failure.model = use_model
                    self._tls.last_failure.request_id = str(getattr(error, "request_id", "") or "")
                    if isinstance(body, dict):
                        self._tls.last_failure.error_code = str(body.get("code") or "")
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
        route_override: Optional[Dict[str, Any]] = None,
        call_context: Any = None,
    ) -> Optional[str]:
        """请求音频转录，使用与聊天请求相同的连接和调用层策略。"""
        use_client = client
        context = call_context or getattr(self._tls, "recovery_context", None)
        if context is not None and task_key != context.task_key:
            context = None
        route_override = route_override or (context.route if context else None)
        route = route_override or {}
        if route_override:
            use_client = use_client or self._client_for_recovery_route(route_override, context)
            if use_client is None:
                return None
            model = route_override.get("model") or model
        elif task_key and use_client is None:
            use_client, route = self.client_for_task(task_key)
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
            register_settings(self.settings)
            request_id = uuid.uuid4().hex
            started = time.monotonic()
            request_fields = dict(request_id=request_id, task=task_key, model=model, provider=anonymous(route.get('provider_id', '')), attempt=attempt, interface='audio')
            event('api.request', **request_fields)
            try:
                with path.open("rb") as audio_file:
                    if context and not context.before_request():
                        self._failure_and_return(context.stop_reason, retriable=False)
                        self._tls.last_failure.recovery_handled = True
                        return None
                    response = use_client.audio.transcriptions.create(
                        model=model,
                        file=audio_file,
                        response_format="text",
                    )
                text = _content_to_text(response).strip()
                if not text:
                    raise AiResponseError("empty transcription")
                event('api.success', **request_fields, elapsed_ms=round((time.monotonic() - started) * 1000), output_chars=len(text))
                if context:
                    context.record(success=True)
                self._set_failure(None)
                self._record_metrics(
                    attempt=attempt,
                    model=model,
                    task_key=task_key,
                    ok=True,
                )
                return text
            except Exception as error:
                event('api.failed', 40, **request_fields, elapsed_ms=round((time.monotonic() - started) * 1000), status_code=_status_code(error), error_type=type(error).__name__, retryable=is_retriable(error))
                logger.debug('api.exception', exc_info=True)
                message = _error_message(error)
                if context:
                    from core.analysis_recovery import redact
                    message = redact(message, [r.get("api_key") for r in context.routes])
                    context.record(error)
                    self._set_failure(ApiCallFailure(message=message, retriable=False,
                        status_code=_status_code(error), error_code=context.last_error.get("error_code", ""),
                        task_key=task_key, provider_id=route.get("provider_id", ""), model=model,
                        request_id=str(getattr(error, "request_id", "") or ""), recovery_handled=context.active))
                retriable = is_retriable(error)
                self._record_metrics(
                    attempt=attempt,
                    model=model,
                    task_key=task_key,
                    ok=False,
                    error=type(error).__name__,
                    retriable=retriable,
                )
                if context and context.handle_failure(error, manual=getattr(context, "manual", False)):
                    return None
                if not retriable:
                    self._failure_and_return(message, retriable=False)
                    return None
                if not should_retry_call(
                    attempt, call_extra_attempts=cfg.call_extra_attempts
                ):
                    self._failure_and_return(message, retriable=True)
                    return None
                sleep_backoff(attempt, config=cfg, sleeper=sleeper)
