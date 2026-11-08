"""视频分析专用的400恢复策略、持久记录与延迟调度上下文。

等待由分析任务调度器执行；本模块不在线程中sleep，不修改全局供应商。
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import time
import uuid
import heapq
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.model_providers import list_providers, resolve_task_route


DEFAULT_POLICY = {
    "enabled": True, "primary_extra_attempts": 2,
    "delay_mode": "exponential", "delay_seconds": 2.0,
    "delay_cap_seconds": 30.0, "jitter": 0.2,
    "fallback_timing": "after_batch", "fallbacks": [],
    "fallback_attempts": 1, "fallback_concurrency": 2,
    "request_budget": 8, "exclusions": [],
}
STATE_LABELS = {
    "pending": "400待重试", "retrying": "400重试中",
    "waiting_fallback": "等待兜底", "exhausted": "重试已耗尽",
    "excluded": "400已排除自动重试", "disabled": "400自动重试已关闭",
    "cancelled": "400恢复已取消", "interrupted": "已中断，待手动恢复",
    "recovered": "曾遇到400，已恢复",
}
ACTIVE_STATES = ("pending", "retrying", "waiting_fallback")


def now():
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any, secrets=()) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "<REDACTED>")
    text = re.sub(r"data:[^\s\"']+", "<IMAGE_REDACTED>", text, flags=re.I)
    text = re.sub(r"(?i)(bearer\s+|api[_-]?key[\s\"':=]+)[^\s,\"'}]+", r"\1<REDACTED>", text)
    text = re.sub(r"https?://[^\s\"']+", "<URL_REDACTED>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "<REDACTED>", text)
    return text[:1000]


def validate_policy(policy, settings=None):
    if not isinstance(policy, dict):
        raise ValueError("恢复策略必须是对象")
    unknown = set(policy) - set(DEFAULT_POLICY)
    if unknown:
        raise ValueError("未知恢复设置：" + ", ".join(sorted(unknown)))
    limits = {
        "primary_extra_attempts": (0, 10), "fallback_attempts": (1, 5),
        "fallback_concurrency": (1, 512), "request_budget": (1, 50),
        "delay_seconds": (0, 3600), "delay_cap_seconds": (0, 3600), "jitter": (0, 1),
    }
    for key, (lo, hi) in limits.items():
        if key in policy:
            value = policy[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{key} 必须在 {lo}～{hi} 之间")
            if key in ("primary_extra_attempts", "fallback_attempts", "fallback_concurrency", "request_budget") and int(value) != value:
                raise ValueError(f"{key} 必须为整数")
    if "enabled" in policy and not isinstance(policy["enabled"], bool):
        raise ValueError("enabled 必须为布尔值")
    for key, values in (("delay_mode", ("fixed", "exponential")), ("fallback_timing", ("after_batch", "immediate"))):
        if key in policy and policy[key] not in values:
            raise ValueError(f"无效的 {key}")
    providers = {p["id"] for p in list_providers(settings)} if settings is not None else None
    if "fallbacks" in policy:
        if not isinstance(policy["fallbacks"], list):
            raise ValueError("兜底必须为列表")
        for route in policy["fallbacks"]:
            if not isinstance(route, dict) or not isinstance(route.get("provider_id"), str) or not route["provider_id"].strip() or not isinstance(route.get("model"), str) or not route["model"].strip():
                raise ValueError("每个兜底必须指定供应商和模型")
            if set(route) - {"provider_id", "model", "enabled", "attempts"}:
                raise ValueError("兜底只允许保存供应商ID、模型、启用状态和次数")
            if providers is not None and route["provider_id"] not in providers:
                raise ValueError("兜底供应商已不存在：" + str(route["provider_id"]))
            if "enabled" in route and not isinstance(route["enabled"], bool):
                raise ValueError("兜底启用状态必须为布尔值")
            attempts = route.get("attempts", 1)
            if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 5:
                raise ValueError("兜底尝试次数必须为1～5")
    if "exclusions" in policy:
        if not isinstance(policy["exclusions"], list):
            raise ValueError("排除规则必须为列表")
        for rule in policy["exclusions"]:
            if not isinstance(rule, dict) or not rule or set(rule) - {"task_key", "error_code", "keyword"}:
                raise ValueError("排除规则只允许任务、错误码、关键词，且不能为空")
            if any(not isinstance(v, str) or not v.strip() for v in rule.values()):
                raise ValueError("排除规则的条件必须为非空文本")
    return copy.deepcopy(policy)


def effective_policy(settings, task_key="video_classification", video=None, temporary=None):
    root = settings.get("processing", {}).get("http400_recovery", {})
    result = copy.deepcopy(DEFAULT_POLICY)
    sources = dict.fromkeys(result, "默认")
    for label, override in (("全局", root.get("global", {})),
                            ("任务", root.get("tasks", {}).get(task_key, {})),
                            ("视频", video or {}), ("本次", temporary or {})):
        validate_policy(override)
        for key, value in override.items():
            result[key] = copy.deepcopy(value)
            sources[key] = label
    return result, sources


def route_for_fallback(settings, entry, task_key):
    providers = {p["id"]: p for p in list_providers(settings)}
    provider = providers.get(entry["provider_id"])
    if provider is None:
        raise ValueError("兜底供应商已不存在：" + entry["provider_id"])
    return {"task_key": task_key, "provider_id": provider["id"],
            "display_name": provider.get("display_name", ""),
            "base_url": provider.get("base_url", ""), "api_key": provider.get("api_key", ""),
            "model": entry["model"]}


def route_fingerprint(route):
    # 连接身份只保留摘要，不把API密钥写入缓存键或恢复历史。
    return hashlib.sha256(json.dumps(route, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def install_schema(connection):
    statements = [
        "CREATE TABLE IF NOT EXISTS video_recovery (video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE, state TEXT NOT NULL, round_id TEXT, updated_at TEXT, detail TEXT NOT NULL DEFAULT '{}')",
        "CREATE INDEX IF NOT EXISTS idx_video_recovery_state ON video_recovery(state, video_id)",
        "CREATE TABLE IF NOT EXISTS video_recovery_policy (video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE, policy TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS video_recovery_rounds (id TEXT PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE, started_at TEXT NOT NULL, ended_at TEXT, mode TEXT NOT NULL, policy TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_recovery_round_video ON video_recovery_rounds(video_id, started_at)",
        "CREATE TABLE IF NOT EXISTS video_recovery_attempts (id INTEGER PRIMARY KEY, round_id TEXT NOT NULL REFERENCES video_recovery_rounds(id) ON DELETE CASCADE, created_at TEXT NOT NULL, detail TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_recovery_attempt_round ON video_recovery_attempts(round_id, id)",
    ]
    for statement in statements:
        connection.execute(statement)


def backfill_legacy(connection):
    """只迁移带明确HTTP状态的旧失败，不根据文件名或孤立数字猜测。"""
    cursor = connection.execute("SELECT id,raw_metadata FROM videos WHERE status='failed'")
    while True:
        rows = cursor.fetchmany(200)
        if not rows:
            break
        for row in rows:
            try:
                metadata = json.loads(row[1] or "{}")
                message = metadata.get("last_error", "")
            except (ValueError, TypeError, AttributeError):
                continue
            if not isinstance(message, str) or not re.search(r"\b(?:HTTP\s*|Error code:\s*)400\b", message, re.I):
                continue
            rid, timestamp = uuid.uuid4().hex, now()
            error = {"status_code": 400, "message": redact(message), "legacy": True}
            detail = json.dumps({"last_error": error, "used": 0, "budget": 8}, ensure_ascii=False)
            connection.execute("INSERT OR IGNORE INTO video_recovery VALUES (?,?,?,?,?)", (row[0], "exhausted", rid, timestamp, detail))
            connection.execute("INSERT INTO video_recovery_rounds VALUES (?,?,?,?,?,?)", (rid, row[0], timestamp, timestamp, "legacy", "{}"))
            connection.execute("INSERT INTO video_recovery_attempts(round_id,created_at,detail) VALUES (?,?,?)", (rid, timestamp, json.dumps(error, ensure_ascii=False)))


class RecoveryStore:
    def __init__(self, db):
        self.db = db

    def video_id(self, path):
        rows = self.db.execute_query("SELECT id FROM videos WHERE path=?", (path,))
        if not rows:
            self.db.upsert_video({"path": path, "filename": __import__("os").path.basename(path), "status": "pending"})
            rows = self.db.execute_query("SELECT id FROM videos WHERE path=?", (path,))
        return rows[0]["id"]

    def override(self, path):
        rows = self.db.execute_query("SELECT p.policy FROM video_recovery_policy p JOIN videos v ON v.id=p.video_id WHERE v.path=?", (path,))
        return json.loads(rows[0]["policy"]) if rows else {}

    def save_override(self, paths, policy, settings):
        validate_policy(policy, settings)
        ids = [self.video_id(path) for path in paths]
        with self.db.lock, self.db._managed_connection() as conn:
            conn.executemany("INSERT INTO video_recovery_policy VALUES (?, ?) ON CONFLICT(video_id) DO UPDATE SET policy=excluded.policy",
                             [(vid, json.dumps(policy, ensure_ascii=False)) for vid in ids])

    def start(self, ctx):
        vid = self.video_id(ctx.path)
        self.db.execute_non_query("INSERT INTO video_recovery_rounds(id,video_id,started_at,mode,policy) VALUES (?,?,?,?,?)",
                                  (ctx.round_id, vid, now(), ctx.mode, json.dumps(ctx.policy, ensure_ascii=False)))
        self.state(ctx, "pending")

    def state(self, ctx, state):
        ctx.state = state
        detail = ctx.detail()
        vid = self.video_id(ctx.path)
        with self.db.lock, self.db._managed_connection() as conn:
            conn.execute("INSERT INTO video_recovery VALUES (?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET state=excluded.state, round_id=excluded.round_id, updated_at=excluded.updated_at, detail=excluded.detail",
                         (vid, state, ctx.round_id, now(), json.dumps(detail, ensure_ascii=False)))
            if state not in ACTIVE_STATES:
                conn.execute("UPDATE video_recovery_rounds SET ended_at=? WHERE id=?", (now(), ctx.round_id))
        callback = getattr(ctx, "on_state", None)
        if callback:
            callback(ctx.path, state)

    def attempt(self, ctx, detail):
        self.db.execute_non_query("INSERT INTO video_recovery_attempts(round_id,created_at,detail) VALUES (?,?,?)",
                                  (ctx.round_id, now(), json.dumps(detail, ensure_ascii=False)))

    def current(self, path):
        rows = self.db.execute_query("SELECT r.* FROM video_recovery r JOIN videos v ON r.video_id=v.id WHERE v.path=?", (path,))
        if not rows:
            return None
        result = dict(rows[0])
        result["detail"] = json.loads(result["detail"])
        return result

    def detail(self, path):
        result = self.current(path)
        if not result:
            return None
        result["rounds"] = self.db.execute_query("SELECT id,started_at,ended_at,mode FROM video_recovery_rounds WHERE video_id=? ORDER BY started_at DESC LIMIT 50", (result["video_id"],))
        result["attempts"] = self.db.execute_query("SELECT a.created_at,a.detail FROM video_recovery_attempts a JOIN video_recovery_rounds r ON r.id=a.round_id WHERE r.video_id=? ORDER BY a.id DESC LIMIT 200", (result["video_id"],))
        for attempt in result["attempts"]:
            attempt["detail"] = json.loads(attempt["detail"])
        return result

    def interrupt(self):
        with self.db.lock, self.db._managed_connection() as conn:
            conn.execute("UPDATE video_recovery_rounds SET ended_at=? WHERE id IN (SELECT round_id FROM video_recovery WHERE state IN ('pending','retrying','waiting_fallback'))", (now(),))
            conn.execute("UPDATE video_recovery SET state='interrupted',updated_at=? WHERE state IN ('pending','retrying','waiting_fallback')", (now(),))


class RecoveryDeferred(Exception):
    def __init__(self, context, delay=0, after_batch=False):
        self.context = context
        self.delay = delay
        self.after_batch = after_batch
        super().__init__("400恢复等待调度")


@dataclass
class RecoveryContext:
    path: str
    settings: dict
    policy: dict
    store: RecoveryStore
    mode: str = "policy"
    selected: dict | None = None
    selected_task: str = "video_classification"
    task_key: str = "video_classification"
    round_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    active: bool = False
    state: str = ""
    used: int = 0
    route_index: int = 0
    route_attempts: int = 0
    last_error: dict = field(default_factory=dict)
    successful_route: dict = field(default_factory=dict)
    successful_routes: dict = field(default_factory=dict)
    analysis_data: Any = None
    video_override: dict = field(default_factory=dict)
    temporary_override: dict = field(default_factory=dict)
    compatibility: set = field(default_factory=set)
    auth_blocked: set = field(default_factory=set)
    random_value: Any = random.random
    stop_reason: str = "400恢复请求预算已耗尽"

    def __post_init__(self):
        self.routes = [resolve_task_route(self.settings, self.task_key)]
        if self.task_key == "content_description":
            self.routes[0]["model"] = "whisper-1"
        self.limits = [int(self.policy["primary_extra_attempts"]) + 1]
        self.invalid_routes = []
        entries = self.policy["fallbacks"] if self.mode != "primary" else []
        if self.mode == "selected" and self.task_key == self.selected_task:
            entries = [self.selected]
            self.routes, self.limits = [], []
        for entry in entries:
            if entry and entry.get("enabled", True):
                try:
                    self.routes.append(route_for_fallback(self.settings, entry, self.task_key))
                    self.limits.append(int(entry.get("attempts", self.policy["fallback_attempts"])))
                except ValueError as exc:
                    self.invalid_routes.append(str(exc))
        if not self.routes:
            raise ValueError("没有可用的恢复API")

    def enter_task(self, task_key):
        if self.task_key == task_key:
            return
        policy, _ = effective_policy(self.settings, task_key, self.video_override, self.temporary_override)
        if self.active:
            policy["request_budget"] = min(policy["request_budget"], self.policy["request_budget"])
        self.task_key, self.policy = task_key, policy
        self.route_index, self.route_attempts = 0, 0
        self.compatibility.clear()
        self.__post_init__()

    @property
    def route(self):
        return self.routes[self.route_index]

    def detail(self):
        return {"task_key": self.task_key, "used": self.used,
                "budget": self.policy["request_budget"], "last_error": self.last_error,
                "route_index": self.route_index, "successful_route": self.successful_route,
                "successful_routes": self.successful_routes,
                "invalid_routes": self.invalid_routes}

    def activate(self):
        if not self.active:
            self.active = True
            self.store.start(self)

    def before_request(self):
        connection = (self.route.get("base_url"), self.route.get("api_key"))
        if connection in self.auth_blocked:
            self.stop_reason = "本轮该连接已经鉴权失败，已跳过"
            class AlreadyUnauthorized(Exception):
                status_code = 401
            self.handle_failure(AlreadyUnauthorized("本轮该连接已经鉴权失败，跳过"), manual=True)
            return False
        if self.active:
            if self.used >= self.policy["request_budget"]:
                self.store.state(self, "exhausted")
                return False
            self.used += 1
            self.store.state(self, "retrying")
        self.route_attempts += 1
        return True

    def record(self, error=None, *, success=False, compatibility=False):
        from core.ai_gateway import _status_code
        code = _status_code(error) if error else None
        body = getattr(error, "body", None)
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            body = body["error"]
        error_code = str(body.get("code") or "") if isinstance(body, dict) else ""
        detail = {"provider_id": self.route.get("provider_id", ""), "model": self.route.get("model", ""),
                  "task_key": self.task_key, "status_code": code, "error_code": error_code,
                  "message": redact(error, [r.get("api_key") for r in self.routes]),
                  "request_id": redact(getattr(error, "request_id", "")),
                  "success": success, "compatibility": compatibility}
        if code == 400:
            if not self.active:
                self.route_attempts = 1
            self.last_error = detail
            self.activate()
        if self.active:
            self.store.attempt(self, detail)
        if success:
            self.successful_route = {k: detail[k] for k in ("provider_id", "model", "task_key")}
            self.successful_routes[self.task_key] = self.successful_route
        elif self.active:
            self.last_error = detail
        return detail

    def handle_failure(self, error, *, manual=False):
        from core.analysis_job_policy import is_retriable
        from core.ai_gateway import _status_code
        if not self.active:
            return False
        if isinstance(error, (FileNotFoundError, PermissionError)):
            self.store.state(self, "exhausted")
            return True
        if self.mode == "policy" and not manual:
            if not self.policy["enabled"]:
                self.store.state(self, "disabled")
                return True
            for rule in self.policy["exclusions"]:
                if all((str(self.last_error.get(k, "")).lower() == v.lower()) if k != "keyword"
                       else v.lower() in self.last_error.get("message", "").lower() for k, v in rule.items()):
                    self.store.state(self, "excluded")
                    return True
        if self.used >= self.policy["request_budget"]:
            self.store.state(self, "exhausted")
            return True
        retryable = _status_code(error) == 400 or is_retriable(error)
        if _status_code(error) in (401, 403):
            self.auth_blocked.add((self.route.get("base_url"), self.route.get("api_key")))
        if retryable and self.route_attempts < self.limits[self.route_index]:
            exponent = max(0, self.route_attempts - 1) if self.policy["delay_mode"] == "exponential" else 0
            delay = min(self.policy["delay_cap_seconds"], self.policy["delay_seconds"] * 2 ** exponent)
            delay *= 1 + self.policy["jitter"] * (2 * self.random_value() - 1)
            delay = min(delay, self.policy["delay_cap_seconds"])
            self.store.state(self, "pending")
            raise RecoveryDeferred(self, max(0, delay))
        next_index = self.route_index + 1
        while next_index < len(self.routes) and (self.routes[next_index].get("base_url"), self.routes[next_index].get("api_key")) in self.auth_blocked:
            next_index += 1
        if next_index < len(self.routes):
            first_fallback = self.route_index == 0 and self.mode != "selected"
            self.route_index = next_index
            self.route_attempts = 0
            self.store.state(self, "waiting_fallback")
            raise RecoveryDeferred(self, after_batch=first_fallback and self.policy["fallback_timing"] == "after_batch")
        self.store.state(self, "exhausted")
        return True


class RecoveryScheduler:
    """仅协调线程等待截止时间；工作线程只做抽帧和请求。"""
    def __init__(self, contexts, cancel_event, *, clock=time.monotonic, executor_factory=ThreadPoolExecutor):
        self.contexts = contexts
        self.cancel_event = cancel_event
        self.clock = clock
        self.executor_factory = executor_factory
        self.after_batch = []

    def run(self, paths, process, *, max_workers, on_wait=None):
        queue = [(self.clock(), i, path) for i, path in enumerate(paths)]
        heapq.heapify(queue)
        serial = len(queue)
        with self.executor_factory(max_workers=max_workers) as executor:
            futures = {}
            while queue or futures:
                cancelled = self.cancel_event.is_set()
                if cancelled:
                    while queue:
                        _, _, path = heapq.heappop(queue)
                        ctx = self.contexts[path]
                        if ctx.active:
                            ctx.store.state(ctx, "cancelled")
                        yield path, None, "用户取消"
                while queue and len(futures) < max_workers and queue[0][0] <= self.clock() and not cancelled:
                    _, _, path = heapq.heappop(queue)
                    futures[executor.submit(process, path)] = path
                if not futures:
                    if queue:
                        self.cancel_event.wait(min(0.1, max(0, queue[0][0] - self.clock())))
                    continue
                timeout = min(0.1, max(0, queue[0][0] - self.clock())) if queue and len(futures) < max_workers else 0.1
                done, _ = wait(futures, timeout=timeout, return_when=FIRST_COMPLETED)
                for future in done:
                    path = futures.pop(future)
                    ctx = self.contexts[path]
                    try:
                        result, error = future.result()
                    except RecoveryDeferred as deferred:
                        if self.cancel_event.is_set():
                            ctx.store.state(ctx, "cancelled")
                            yield path, None, "用户取消"
                        elif deferred.after_batch:
                            self.after_batch.append(path)
                            if on_wait:
                                on_wait(path, ctx)
                            yield path, None, deferred
                        else:
                            serial += 1
                            heapq.heappush(queue, (self.clock() + deferred.delay, serial, path))
                            if on_wait:
                                on_wait(path, ctx)
                        continue
                    except Exception as exc:
                        result, error = None, redact(exc)
                    if ctx.active and self.cancel_event.is_set() and result is None:
                        ctx.store.state(ctx, "cancelled")
                    elif ctx.active and result is None and ctx.state in ACTIVE_STATES:
                        ctx.last_error = {**ctx.last_error, "message": redact(error)}
                        ctx.store.state(ctx, "exhausted")
                    yield path, result, error
