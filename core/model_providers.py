# -*- coding: utf-8 -*-
"""模型供应商档案：迁移、上限、当前解析与扁平 api 写穿（纯逻辑，可单测）。"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List, Optional, Tuple

MAX_PROVIDERS = 5
DEFAULT_DISPLAY_NAME = "默认"
LEGACY_DEFAULT_BASE_URL = "https://vqnypowwewrv.ap-northeast-1.clawcloudrun.com/v1"
DEFAULT_MODEL = "gemini-2.0-flash"

MODEL_KEYS = (
    "video_classification",
    "tag_generation",
    "content_description",
)


class ProviderLimitError(ValueError):
    """已达供应商档案上限（最多五个）。"""


class ProviderError(ValueError):
    """供应商档案操作失败。"""


def default_models() -> Dict[str, str]:
    return {
        "video_classification": DEFAULT_MODEL,
        "tag_generation": DEFAULT_MODEL,
        "content_description": DEFAULT_MODEL,
    }


def _normalize_models(raw: Any) -> Dict[str, str]:
    base = default_models()
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    for key in MODEL_KEYS:
        val = raw.get(key)
        if val is not None and str(val).strip():
            out[key] = str(val).strip()
    return out


def new_provider_id() -> str:
    return f"p_{uuid.uuid4().hex[:12]}"


def make_provider(
    *,
    provider_id: Optional[str] = None,
    display_name: str = DEFAULT_DISPLAY_NAME,
    api_key: str = "",
    base_url: str = "",
    models: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    return {
        "id": provider_id or new_provider_id(),
        "display_name": (display_name or DEFAULT_DISPLAY_NAME).strip() or DEFAULT_DISPLAY_NAME,
        "api_key": api_key or "",
        "base_url": base_url or "",
        "models": _normalize_models(models),
    }


def list_providers(settings: Dict) -> List[Dict[str, Any]]:
    raw = settings.get("model_providers")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip() or new_provider_id()
        out.append(
            make_provider(
                provider_id=pid,
                display_name=str(item.get("display_name") or DEFAULT_DISPLAY_NAME),
                api_key=str(item.get("api_key") or ""),
                base_url=str(item.get("base_url") or ""),
                models=item.get("models"),
            )
        )
    return out


def _get_api_section(settings: Dict) -> Dict[str, Any]:
    api = settings.get("api")
    if not isinstance(api, dict):
        api = {}
        settings["api"] = api
    return api


def _legacy_models_from_api(settings: Dict) -> Dict[str, str]:
    api = _get_api_section(settings)
    mp = api.get("model_personalization")
    return _normalize_models(mp if isinstance(mp, dict) else None)


def migrate_legacy_api_to_providers(settings: Dict) -> Dict:
    """
    无档案时：从旧 api.key / api.base_url / api.model_personalization
    迁成显示名「默认」并标为当前。
    已有档案则原样返回。
    """
    providers = list_providers(settings)
    if providers:
        settings["model_providers"] = providers
        return settings

    api = _get_api_section(settings)
    provider = make_provider(
        display_name=DEFAULT_DISPLAY_NAME,
        api_key=str(api.get("key") or ""),
        base_url=str(api.get("base_url") or LEGACY_DEFAULT_BASE_URL),
        models=_legacy_models_from_api(settings),
    )
    settings["model_providers"] = [provider]
    settings["current_provider_id"] = provider["id"]
    return settings


def ensure_providers(settings: Dict) -> Dict:
    """保证至少一条档案、当前 id 有效，并写穿扁平 api 字段。"""
    if not isinstance(settings, dict):
        raise TypeError("settings must be a dict")

    migrate_legacy_api_to_providers(settings)
    providers = list_providers(settings)

    if not providers:
        # 极端兜底：可编辑空档案
        empty = make_provider(display_name=DEFAULT_DISPLAY_NAME)
        providers = [empty]
        settings["current_provider_id"] = empty["id"]

    # 规范化列表（补全字段）
    settings["model_providers"] = providers

    current_id = str(settings.get("current_provider_id") or "").strip()
    ids = {p["id"] for p in providers}
    if not current_id or current_id not in ids:
        settings["current_provider_id"] = providers[0]["id"]

    apply_provider_to_api_flat(settings)
    return settings


def find_provider(settings: Dict, provider_id: str) -> Optional[Dict[str, Any]]:
    pid = str(provider_id or "").strip()
    for p in list_providers(settings):
        if p["id"] == pid:
            return p
    return None


def resolve_current_provider(settings: Dict) -> Dict[str, Any]:
    """返回当前供应商档案的深拷贝（保证存在）。"""
    ensure_providers(settings)
    current_id = str(settings.get("current_provider_id") or "")
    for p in list_providers(settings):
        if p["id"] == current_id:
            return copy.deepcopy(p)
    # 不应到达；兜底第一条
    providers = list_providers(settings)
    return copy.deepcopy(providers[0])


def apply_provider_to_api_flat(settings: Dict) -> Dict:
    """把当前档案写穿到 api.key / base_url / model_personalization，供 AIHandler 读。"""
    providers = list_providers(settings)
    if not providers:
        return settings

    current_id = str(settings.get("current_provider_id") or "").strip()
    provider = None
    for p in providers:
        if p["id"] == current_id:
            provider = p
            break
    if provider is None:
        provider = providers[0]
        settings["current_provider_id"] = provider["id"]

    api = _get_api_section(settings)
    api["key"] = provider.get("api_key") or ""
    api["base_url"] = provider.get("base_url") or ""
    api["model_personalization"] = _normalize_models(provider.get("models"))
    return settings


def set_current_provider(settings: Dict, provider_id: str) -> Dict:
    pid = str(provider_id or "").strip()
    if not find_provider(settings, pid):
        raise ProviderError(f"供应商不存在: {pid}")
    settings["current_provider_id"] = pid
    apply_provider_to_api_flat(settings)
    return settings


def update_provider(
    settings: Dict,
    provider_id: str,
    *,
    display_name: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    models: Optional[Dict[str, str]] = None,
) -> Dict:
    ensure_providers(settings)
    providers = list_providers(settings)
    found = False
    for i, p in enumerate(providers):
        if p["id"] != provider_id:
            continue
        if display_name is not None:
            p["display_name"] = (display_name or DEFAULT_DISPLAY_NAME).strip() or DEFAULT_DISPLAY_NAME
        if api_key is not None:
            p["api_key"] = api_key or ""
        if base_url is not None:
            p["base_url"] = base_url or ""
        if models is not None:
            p["models"] = _normalize_models(models)
        providers[i] = p
        found = True
        break
    if not found:
        raise ProviderError(f"供应商不存在: {provider_id}")
    settings["model_providers"] = providers
    if str(settings.get("current_provider_id") or "") == provider_id:
        apply_provider_to_api_flat(settings)
    return settings


def add_provider(
    settings: Dict,
    *,
    display_name: str = "新供应商",
    api_key: str = "",
    base_url: str = "",
    models: Optional[Dict[str, str]] = None,
    set_as_current: bool = False,
) -> Tuple[Dict, Dict[str, Any]]:
    """新增档案；已满五个时抛 ProviderLimitError。"""
    ensure_providers(settings)
    providers = list_providers(settings)
    if len(providers) >= MAX_PROVIDERS:
        raise ProviderLimitError(f"最多只能保存 {MAX_PROVIDERS} 个模型供应商")

    # 新档案默认继承当前的 base_url / 模型，便于少填
    current = resolve_current_provider(settings)
    provider = make_provider(
        display_name=display_name or "新供应商",
        api_key=api_key if api_key is not None else "",
        base_url=base_url if base_url else (current.get("base_url") or ""),
        models=models if models is not None else current.get("models"),
    )
    providers.append(provider)
    settings["model_providers"] = providers
    if set_as_current or not settings.get("current_provider_id"):
        settings["current_provider_id"] = provider["id"]
        apply_provider_to_api_flat(settings)
    return settings, provider


def remove_provider(settings: Dict, provider_id: str) -> Dict:
    """
    删除档案。删当前则自动切到另一条。
    至少保留一条：只剩一条时拒绝删除。
    """
    ensure_providers(settings)
    providers = list_providers(settings)
    pid = str(provider_id or "").strip()
    if not any(p["id"] == pid for p in providers):
        raise ProviderError(f"供应商不存在: {pid}")
    if len(providers) <= 1:
        raise ProviderError("至少保留一个模型供应商档案")

    new_list = [p for p in providers if p["id"] != pid]
    settings["model_providers"] = new_list
    current_id = str(settings.get("current_provider_id") or "")
    if current_id == pid or current_id not in {p["id"] for p in new_list}:
        settings["current_provider_id"] = new_list[0]["id"]
    apply_provider_to_api_flat(settings)
    return settings


def replace_providers(settings: Dict, providers: List[Dict[str, Any]], current_id: str) -> Dict:
    """整表替换（设置 UI 保存用）。超过上限截断前 MAX_PROVIDERS 条。"""
    cleaned: List[Dict[str, Any]] = []
    for item in providers or []:
        if not isinstance(item, dict):
            continue
        cleaned.append(
            make_provider(
                provider_id=str(item.get("id") or "") or None,
                display_name=str(item.get("display_name") or DEFAULT_DISPLAY_NAME),
                api_key=str(item.get("api_key") or ""),
                base_url=str(item.get("base_url") or ""),
                models=item.get("models"),
            )
        )
        if len(cleaned) >= MAX_PROVIDERS:
            break
    if not cleaned:
        cleaned = [make_provider(display_name=DEFAULT_DISPLAY_NAME)]
    settings["model_providers"] = cleaned
    cid = str(current_id or "").strip()
    ids = {p["id"] for p in cleaned}
    settings["current_provider_id"] = cid if cid in ids else cleaned[0]["id"]
    apply_provider_to_api_flat(settings)
    return settings