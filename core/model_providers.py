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

# 任务模型路由六槽（ADR-0006）；冷启动不占槽
TASK_ROUTE_KEYS = (
    "video_classification",
    "tag_generation",
    "content_description",
    "pending_tag_ai",
    "synonym_audit",
    "standard_tag_ai",
)

TASK_ROUTE_LABELS = {
    "video_classification": "视频分类",
    "tag_generation": "标签生成",
    "content_description": "内容描述",
    "pending_tag_ai": "待审词 AI",
    "synonym_audit": "近义巡检",
    "standard_tag_ai": "标准词 AI 助手",
}


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


def empty_route_slot() -> Dict[str, str]:
    return {"provider_id": "", "model": ""}


def _normalize_route_slot(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return empty_route_slot()
    return {
        "provider_id": str(raw.get("provider_id") or "").strip(),
        "model": str(raw.get("model") or "").strip(),
    }


def get_task_model_routing(settings: Dict) -> Dict[str, Dict[str, str]]:
    raw = settings.get("task_model_routing")
    if not isinstance(raw, dict):
        return {k: empty_route_slot() for k in TASK_ROUTE_KEYS}
    out: Dict[str, Dict[str, str]] = {}
    for key in TASK_ROUTE_KEYS:
        out[key] = _normalize_route_slot(raw.get(key))
    return out


def set_task_route(
    settings: Dict,
    task_key: str,
    *,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict:
    """更新单个路由槽（内存）。"""
    if task_key not in TASK_ROUTE_KEYS:
        raise ProviderError(f"未知任务路由槽: {task_key}")
    ensure_providers(settings)
    routing = get_task_model_routing(settings)
    slot = dict(routing.get(task_key) or empty_route_slot())
    if provider_id is not None:
        slot["provider_id"] = str(provider_id or "").strip()
    if model is not None:
        slot["model"] = str(model or "").strip()
    routing[task_key] = slot
    settings["task_model_routing"] = routing
    return settings


def replace_task_model_routing(
    settings: Dict,
    routing: Dict[str, Any],
) -> Dict:
    """整表替换路由（设置 UI 保存用）。"""
    ensure_providers(settings)
    cleaned: Dict[str, Dict[str, str]] = {}
    src = routing if isinstance(routing, dict) else {}
    for key in TASK_ROUTE_KEYS:
        cleaned[key] = _normalize_route_slot(src.get(key))
    settings["task_model_routing"] = cleaned
    return settings


def migrate_task_model_routing(settings: Dict) -> Dict:
    """
    无路由表或槽不全时：分析三槽从当前供应商 models 填入；
    标签库三槽空 provider_id（回退当前供应商）。
    """
    providers = list_providers(settings)
    if not providers:
        return settings

    current_id = str(settings.get("current_provider_id") or "").strip()
    ids = {p["id"] for p in providers}
    if not current_id or current_id not in ids:
        current_id = providers[0]["id"]

    current = next((p for p in providers if p["id"] == current_id), providers[0])
    models = _normalize_models(current.get("models"))

    existing = settings.get("task_model_routing")
    routing: Dict[str, Dict[str, str]] = {}
    if isinstance(existing, dict):
        for key in TASK_ROUTE_KEYS:
            if key in existing:
                routing[key] = _normalize_route_slot(existing.get(key))

    # 首次或缺失分析槽：写入当前供应商 + 档案内模型名
    for key in MODEL_KEYS:
        if key not in routing:
            routing[key] = {
                "provider_id": current_id,
                "model": models.get(key) or DEFAULT_MODEL,
            }
        else:
            # 有槽但 model 空：用档案默认
            if not routing[key].get("model"):
                routing[key]["model"] = models.get(key) or DEFAULT_MODEL

    for key in TASK_ROUTE_KEYS:
        if key not in routing:
            routing[key] = empty_route_slot()

    settings["task_model_routing"] = routing
    return settings


def resolve_task_route(settings: Dict, task_key: str) -> Dict[str, Any]:
    """
    解析任务 → 供应商连接 + 模型名。

    返回: task_key, provider_id, display_name, api_key, base_url, model
    provider_id 空或无效 → 当前供应商；model 空 → 档案 models 或 DEFAULT_MODEL。
    """
    ensure_providers(settings)
    if task_key not in TASK_ROUTE_KEYS:
        # 未知槽：按当前供应商 + 默认模型
        cur = resolve_current_provider(settings)
        return {
            "task_key": task_key,
            "provider_id": cur["id"],
            "display_name": cur.get("display_name") or "",
            "api_key": cur.get("api_key") or "",
            "base_url": cur.get("base_url") or "",
            "model": DEFAULT_MODEL,
        }

    routing = get_task_model_routing(settings)
    slot = routing.get(task_key) or empty_route_slot()
    providers = list_providers(settings)
    by_id = {p["id"]: p for p in providers}
    current_id = str(settings.get("current_provider_id") or "").strip()
    if current_id not in by_id and providers:
        current_id = providers[0]["id"]

    pid = str(slot.get("provider_id") or "").strip()
    if not pid or pid not in by_id:
        pid = current_id
    provider = by_id.get(pid) or resolve_current_provider(settings)

    model = str(slot.get("model") or "").strip()
    if not model:
        p_models = _normalize_models(provider.get("models"))
        if task_key in MODEL_KEYS:
            model = p_models.get(task_key) or DEFAULT_MODEL
        else:
            # 标签库 AI：优先档案 tag_generation，再默认
            model = p_models.get("tag_generation") or DEFAULT_MODEL

    return {
        "task_key": task_key,
        "provider_id": provider.get("id") or pid,
        "display_name": provider.get("display_name") or "",
        "api_key": provider.get("api_key") or "",
        "base_url": provider.get("base_url") or "",
        "model": model,
    }


def ensure_providers(settings: Dict) -> Dict:
    """保证至少一条档案、当前 id 有效，任务路由齐全，并写穿扁平 api 字段。"""
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

    migrate_task_model_routing(settings)
    # 删供应商后：指向失效 id 的槽清空 provider_id（回退当前）
    routing = get_task_model_routing(settings)
    changed = False
    for key, slot in routing.items():
        pid = str(slot.get("provider_id") or "").strip()
        if pid and pid not in ids:
            slot["provider_id"] = ""
            routing[key] = slot
            changed = True
    if changed:
        settings["task_model_routing"] = routing

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