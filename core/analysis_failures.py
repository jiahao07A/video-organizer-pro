"""分析失败的只读分类、索引查询和按需诊断；不决定自动重试策略。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from core.analysis_recovery import redact, now, route_fingerprint, validate_policy, effective_policy, route_for_fallback
from core.model_providers import resolve_task_route, list_providers

CATEGORIES = {
    "auth": "鉴权/权限", "quota": "余额/配额", "rate_limit": "限流",
    "network": "网络/超时", "service": "服务不可用", "model": "模型或接口不兼容",
    "input": "参数/输入限制", "refusal": "内容拒绝", "response": "响应为空/解析失败",
    "file": "文件读取/解码", "storage": "结果保存", "unknown": "未知错误",
}
STAGES = {"extract": "文件读取/抽帧", "video_classification": "视频主分析",
          "content_description": "音频转录", "normalize": "结果归一", "save": "结果保存", "unknown": "未知阶段"}
SUGGESTIONS = {
    "auth": "检查密钥和接口权限，修正连接后重试，或选择其他连接。",
    "quota": "检查余额和额度，补充额度或更换可用连接。",
    "rate_limit": "降低并发、增加请求间隔后重试。",
    "network": "检查网络连接，增加间隔后重试。",
    "service": "等待服务恢复，或指定备用连接重试。",
    "model": "核对模型名称及视觉/音频接口能力，选择兼容模型。",
    "input": "核对服务端参数和输入大小限制；可选择兼容连接。",
    "refusal": "核查服务端内容限制；不能据此判定素材损坏。",
    "response": "服务返回内容无法解析，可重试或更换兼容模型。",
    "file": "检查素材是否存在、可读取及可正常解码。",
    "storage": "检查数据目录权限和可用空间后重试。",
    "unknown": "查看错误详情及历史，必要时主动进行AI诊断。",
}


def failure_state_label(state):
    """统一失败中心的恢复状态不包含400历史断言。"""
    return {"pending": "待重试", "retrying": "重试中", "waiting_fallback": "等待备用API",
            "exhausted": "重试已耗尽", "excluded": "已排除自动重试", "disabled": "自动重试已关闭",
            "cancelled": "已取消", "interrupted": "已中断，待手动恢复", "recovered": "已恢复",
            "failed": "分析失败，待处理"}.get(state, state or "未知")


def valid_diagnosis(value):
    """新响应及旧缓存都必须包含有内容的原因、依据和结构正确的建议。"""
    return (isinstance(value, dict)
            and all(isinstance(value.get(key), str) and value[key].strip() for key in ("cause", "evidence"))
            and isinstance(value.get("actions"), list)
            and all(isinstance(action, str) and action.strip() for action in value["actions"])
            and isinstance(value.get("retry_options", {}), dict))


def failure_time_range(since="", until=""):
    """UTC输入；日期结束用次日零点的排他边界，避免丢失当天或包含次日。"""
    def parse(value, label, end=False):
        if not value:
            return None, False
        value = value.strip()
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].+)?", value):
                raise ValueError()
            date_only = len(value) == 10
            parsed = datetime.fromisoformat(value)
            if date_only:
                parsed = datetime.combine(parsed.date(), time(), tzinfo=timezone.utc)
                if end:
                    parsed += timedelta(days=1)
            elif parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc), date_only and end
        except (ValueError, OverflowError):
            raise ValueError(f"{label}格式无效，请输入YYYY-MM-DD或ISO时间（无时区时按UTC）。") from None
    start, _ = parse(since, "开始时间")
    end, exclusive = parse(until, "结束时间", end=True)
    if start is not None and end is not None and (start >= end if exclusive else start > end):
        raise ValueError("开始时间不能晚于结束时间。")
    return start, end, exclusive


def diagnostic_text(value, settings=None):
    secrets = [p.get("api_key") for p in list_providers(settings or {})]
    text = redact(value, secrets)
    text = re.sub(r"(?i)[a-z]:[\\/][^\n\r\"'<>]+", "<PATH>", text)
    text = re.sub(r"(?<!\w)/(?:[^\s/]+/)+[^\s\"']*", "<PATH>", text)
    text = re.sub(r"\b[A-Za-z0-9+/]{120,}={0,2}", "<DATA>", text)
    return text


def classify(error):
    error = error or {}
    message = str(error.get("message") or "")
    code = error.get("status_code")
    try:
        code = int(code) if code is not None else None
    except (ValueError, TypeError):
        code = None
    if code is None:
        match = re.search(r"\b(?:HTTP\s*|Error code:\s*)([45]\d\d)\b", message, re.I)
        code = int(match[1]) if match else None
    text = (str(error.get("error_code") or "") + " " + str(error.get("exception_type") or "") + " " + message).lower()
    stage = error.get("stage") or error.get("task_key") or "unknown"
    category = "unknown"
    if 'invalid analysis result' in text or 'invalid_analysis_result' in text:
        category = "response"
    elif stage == "save":
        category = "storage"
    elif stage == "extract" or any(x in text for x in ("filenotfounderror", "permissionerror", "文件不存在", "无法抽取", "无法打开视频", "解码", "no such file")):
        category = "file"
    elif any(x in text for x in ("insufficient_quota", "quota_exceeded", "billing", "余额", "额度", "credit balance", "payment required")) or code == 402:
        category = "quota"
    elif code in (401, 403) or any(x in text for x in ("authentication", "invalid_api_key", "unauthorized")):
        category = "auth"
    elif code == 429 or "rate_limit" in text or "rate limit" in text:
        category = "rate_limit"
    elif any(x in text for x in ("unsafe", "content_policy", "content_filter", "refusal", "内容拒绝", "模型拒绝", "审核")):
        category = "refusal"
    elif code in (404, 405, 501) or any(x in text for x in ("model_not_found", "unsupported model", "does not support", "not supported", "unknown model")):
        category = "model"
    elif code in (408, 504) or any(x in text for x in ("timeout", "timed out", "connection", "connecterror", "超时", "连接重置", "网络")):
        category = "network"
    elif code is not None and 500 <= code <= 599:
        category = "service"
    elif any(x in text for x in ("json", "empty body", "empty choices", "empty message", "empty transcription", "解析", "未返回有效", "无效分析", "invalid response")):
        category = "response"
    elif code in (400, 413, 415, 422) and any(x in text for x in ("invalid", "parameter", "argument", "too large", "context_length", "token", "image", "audio", "输入", "参数", "大小")):
        category = "input"
    normalized = re.sub(r"\b[0-9a-f]{16,}\b|\b\d+\b", "#", diagnostic_text(message).lower())
    signature = [stage, error.get("provider_id") or "", error.get("model") or "", code, error.get("error_code") or "", normalized]
    return {"category": category, "stage": stage, "status_code": code,
            "evidence": diagnostic_text(f"HTTP {code or '—'}；{error.get('error_code') or error.get('exception_type') or '错误文本'}；{message}"),
            "suggestion": SUGGESTIONS[category],
            "fingerprint": hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()}


def install_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS analysis_failure_index (
        video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
        category TEXT NOT NULL, stage TEXT NOT NULL, status_code INTEGER,
        provider_id TEXT NOT NULL, model TEXT NOT NULL, message TEXT NOT NULL,
        evidence TEXT NOT NULL, suggestion TEXT NOT NULL, fingerprint TEXT NOT NULL,
        ever400 INTEGER NOT NULL DEFAULT 0, retained_result INTEGER NOT NULL DEFAULT 0,
        attempt_count INTEGER)""")
    for name, cols in (("category", "category,video_id"), ("stage", "stage,video_id"),
                       ("route", "provider_id,model,video_id"), ("http", "status_code,ever400,video_id"),
                       ("fingerprint", "fingerprint")):
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_analysis_failure_{name} ON analysis_failure_index({cols})")
    conn.execute("""CREATE TABLE IF NOT EXISTS analysis_failure_diagnoses (
        fingerprint TEXT NOT NULL, route_hash TEXT NOT NULL, created_at TEXT NOT NULL,
        result TEXT NOT NULL, PRIMARY KEY(fingerprint,route_hash))""")


def refresh_index(conn, video_id):
    row = conn.execute("SELECT r.detail,v.status FROM video_recovery r JOIN videos v ON v.id=r.video_id WHERE r.video_id=?", (video_id,)).fetchone()
    if row is None:
        return
    info = json.loads(row[0] or "{}")
    error = info.get("last_error") or {}
    result = classify(error)
    old = conn.execute("SELECT ever400 FROM analysis_failure_index WHERE video_id=?", (video_id,)).fetchone()
    ever = bool((old and old[0]) or info.get("ever400") or result["status_code"] == 400)
    if not ever:
        # 最后一次错误可能已变化；历史中明确的400仍是不可丢失的事实。
        ever = conn.execute("""SELECT 1 FROM video_recovery_attempts a
            JOIN video_recovery_rounds r ON r.id=a.round_id
            WHERE r.video_id=? AND json_valid(a.detail)
            AND CAST(json_extract(a.detail,'$.status_code') AS TEXT)='400' LIMIT 1""", (video_id,)).fetchone() is not None
    conn.execute("""INSERT INTO analysis_failure_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(video_id) DO UPDATE SET category=excluded.category,stage=excluded.stage,
        status_code=excluded.status_code,provider_id=excluded.provider_id,model=excluded.model,
        message=excluded.message,evidence=excluded.evidence,suggestion=excluded.suggestion,
        fingerprint=excluded.fingerprint,ever400=excluded.ever400,
        retained_result=excluded.retained_result,attempt_count=excluded.attempt_count""",
        (video_id, result["category"], result["stage"], result["status_code"], error.get("provider_id") or "",
         error.get("model") or "", diagnostic_text(error.get("message")), result["evidence"], result["suggestion"],
         result["fingerprint"], int(ever), int(row[1] in ("analyzed", "renamed", "edited")), info.get("attempt_count")))


def backfill(conn):
    from core.analysis_recovery import now
    import uuid
    for row in conn.execute("SELECT id,raw_metadata FROM videos WHERE status='failed' AND id NOT IN (SELECT video_id FROM video_recovery)").fetchall():
        try:
            error = json.loads(row[1] or "{}").get("last_error")
        except (ValueError, AttributeError):
            error = None
        if not isinstance(error, (str, dict)) or not error:
            continue
        rid, timestamp = uuid.uuid4().hex, now()
        structured = dict(error) if isinstance(error, dict) else {"message": error}
        structured["message"] = diagnostic_text(structured.get("message", ""))
        structured["legacy"] = True
        info = {"last_error": structured, "attempt_count": None}
        conn.execute("INSERT INTO video_recovery VALUES (?,?,?,?,?)", (row[0], "failed", rid, timestamp, json.dumps(info, ensure_ascii=False)))
        conn.execute("INSERT INTO video_recovery_rounds VALUES (?,?,?,?,?,?)", (rid, row[0], timestamp, timestamp, "legacy", "{}"))
    for (vid,) in conn.execute("SELECT video_id FROM video_recovery").fetchall():
        refresh_index(conn, vid)


@dataclass(frozen=True)
class FailureQuery:
    scope_paths: tuple | None = None
    category: str = ""
    stage: str = ""
    status_code: int | None = None
    provider_id: str = ""
    model: str = ""
    state: str = "unresolved"
    since: str = ""
    until: str = ""
    min_attempts: int | None = None
    max_attempts: int | None = None
    keyword: str = ""
    ever400: bool = False
    retained_result: bool = False
    fingerprints: tuple = field(default_factory=tuple)


class FailureStore:
    def __init__(self, db):
        self.db = db

    def _where(self, conn, query):
        clauses, params = [], []
        start, end, exclusive = failure_time_range(query.since, query.until)
        for value, op in ((start, ">="), (end, "<" if exclusive else "<=")):
            if value is not None:
                clauses.append(f"julianday(r.updated_at){op}julianday(?)")
                params.append(value.isoformat())
        if query.scope_paths is not None:
            conn.execute("CREATE TEMP TABLE failure_scope(path TEXT PRIMARY KEY)")
            conn.executemany("INSERT OR IGNORE INTO failure_scope VALUES (?)", ((x,) for x in query.scope_paths))
            clauses.append("v.path IN (SELECT path FROM failure_scope)")
        if query.fingerprints:
            conn.execute("CREATE TEMP TABLE failure_fingerprints(value TEXT PRIMARY KEY)")
            conn.executemany("INSERT OR IGNORE INTO failure_fingerprints VALUES (?)", ((x,) for x in query.fingerprints))
            clauses.append("f.fingerprint IN (SELECT value FROM failure_fingerprints)")
        if query.state == "unresolved":
            clauses.append("r.state<>'recovered'")
        elif query.state:
            clauses.append("r.state=?")
            params.append(query.state)
        for key in ("category", "stage", "status_code", "provider_id", "model"):
            value = getattr(query, key)
            if value is not None and value != "":
                clauses.append(f"f.{key}=?")
                params.append(value)
        for key, op, column in (("min_attempts", ">=", "f.attempt_count"), ("max_attempts", "<=", "f.attempt_count")):
            value = getattr(query, key)
            if value is not None and value != "":
                clauses.append(f"{column}{op}?")
                params.append(value)
        for key in ("ever400", "retained_result"):
            if getattr(query, key):
                clauses.append(f"f.{key}=1")
        if query.keyword:
            clauses.append("instr(lower(f.message || ' ' || v.filename),lower(?))>0")
            params.append(query.keyword)
        return " WHERE " + " AND ".join(clauses) if clauses else "", params

    def query(self, query=FailureQuery(), *, offset=0, limit=100, paths_only=False, groups_only=False):
        with self.db.lock, self.db._managed_connection() as conn:
            where, params = self._where(conn, query)
            base = " FROM analysis_failure_index f JOIN videos v ON v.id=f.video_id JOIN video_recovery r ON r.video_id=f.video_id"
            if paths_only:
                return [x[0] for x in conn.execute("SELECT v.path" + base + where + " ORDER BY r.updated_at DESC,v.id DESC", params)]
            if groups_only:
                return [dict(x) for x in conn.execute("SELECT f.*,COUNT(*) AS material_count" + base + where + " GROUP BY f.fingerprint ORDER BY f.fingerprint", params)]
            total = conn.execute("SELECT COUNT(*)" + base + where, params).fetchone()[0]
            counts = {x[0]: x[1] for x in conn.execute("SELECT f.category,COUNT(*)" + base + where + " GROUP BY f.category", params)}
            rows = [dict(x) for x in conn.execute("SELECT v.path,v.filename,f.*,r.state,r.updated_at,r.round_id" + base + where + " ORDER BY r.updated_at DESC,v.id DESC LIMIT ? OFFSET ?", (*params, min(200, max(1, int(limit))), max(0, int(offset))))]
            return {"rows": rows, "total": total, "counts": counts}

    def diagnosis(self, fingerprint, route_hash):
        rows = self.db.execute_query("SELECT result,created_at FROM analysis_failure_diagnoses WHERE fingerprint=? AND route_hash=?", (fingerprint, route_hash))
        if not rows:
            return None
        return {"result": json.loads(rows[0]["result"]), "created_at": rows[0]["created_at"]}

    def save_diagnosis(self, fingerprint, route_hash, result):
        self.db.execute_non_query("INSERT INTO analysis_failure_diagnoses VALUES (?,?,?,?) ON CONFLICT(fingerprint,route_hash) DO UPDATE SET created_at=excluded.created_at,result=excluded.result", (fingerprint, route_hash, now(), json.dumps(result, ensure_ascii=False)))


def validate_manual_options(options, settings):
    options = copy.deepcopy(options or {})
    if set(options) - {"concurrency", "request_interval", "stage_routes", "request_budgets"}:
        raise ValueError("未知手动重试选项")
    concurrency = options.setdefault("concurrency", 2)
    interval = options.setdefault("request_interval", 2.0)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 32:
        raise ValueError("本次并发必须为1～32")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0 <= interval <= 3600:
        raise ValueError("请求间隔必须为0～3600秒")
    routes = options.setdefault("stage_routes", {})
    if not isinstance(routes, dict) or set(routes) - {"video_classification", "content_description"}:
        raise ValueError("只能指定视频主分析和音频转录连接")
    if "content_description" in routes and not settings.get("processing", {}).get("enable_audio_transcription"):
        raise ValueError("音频转录未启用，不能应用音频连接建议；请先在分析设置中启用音频转录。")
    for entry in routes.values():
        validate_policy({"fallbacks": [entry]}, settings)
    budgets = options.get("request_budgets", {})
    if not isinstance(budgets, dict) or any(not isinstance(path, str) or isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50 for path, value in budgets.items()):
        raise ValueError("冻结请求预算必须为路径与1～50整数的映射")
    return options


def manual_request_budget(settings, video_override=None, temporary=None):
    """手动完整链路共用一个预算，在首个请求前合并各阶段上限。"""
    tasks = ["video_classification"]
    if settings.get("processing", {}).get("enable_audio_transcription"):
        tasks.append("content_description")
    return min(effective_policy(settings, task, video_override, temporary)[0]["request_budget"] for task in tasks)


def preview_retry(service, paths, *, recovery_mode="policy", recovery_override=None, selected_fallback=None, manual_options=None):
    from core.analysis_recovery import RecoveryStore, RecoveryContext
    if recovery_mode not in ("policy", "primary", "selected"):
        raise ValueError("未知手动重试模式")
    override = validate_policy(recovery_override or {}, service.settings)
    options = validate_manual_options(manual_options, service.settings)
    ready, skipped, previews = [], [], []
    store = RecoveryStore(service.db)
    for path in dict.fromkeys(paths):
        try:
            if hasattr(service, 'filter_processable_paths') and not service.filter_processable_paths([path]):
                raise ValueError('重复副本或内容变化待核对，跳过重试')
            if not os.path.isfile(path) or not os.access(path, os.R_OK):
                raise ValueError("文件不存在或不可读取")
            tasks = ["video_classification"]
            if service.settings.get("processing", {}).get("enable_audio_transcription"):
                tasks.append("content_description")
            connections, route_chains = {}, {}
            budget = manual_request_budget(service.settings, store.override(path), override)
            budget = min(budget, options.get("request_budgets", {}).get(path, budget))
            failed_task = (store.current(path) or {}).get("detail", {}).get("last_error", {}).get("task_key") or "video_classification"
            for task in tasks:
                policy, _ = effective_policy(service.settings, task, store.override(path), override)
                entry = options["stage_routes"].get(task)
                if recovery_mode == "selected" and not entry and task == failed_task:
                    entry = selected_fallback
                    if not entry:
                        raise ValueError("请指定失败阶段的API")
                route = route_for_fallback(service.settings, entry, task) if entry else resolve_task_route(service.settings, task)
                if task == "content_description" and not entry:
                    route["model"] = "whisper-1"
                if not route.get("base_url") or not route.get("api_key") or not route.get("model"):
                    raise ValueError(f"{STAGES[task]}连接缺少地址、密钥或模型")
                connections[task] = {k: route.get(k, "") for k in ("provider_id", "model", "display_name")}
                context = RecoveryContext(path, service.settings, policy, store, mode=recovery_mode,
                                          selected=selected_fallback, selected_task=failed_task,
                                          task_key=task, manual_options=options)
                chain = []
                for candidate, attempts in zip(context.routes, context.limits):
                    if not candidate.get("base_url") or not candidate.get("api_key") or not candidate.get("model"):
                        raise ValueError(f"{STAGES[task]}连接缺少地址、密钥或模型")
                    chain.append({**{k: candidate.get(k, "") for k in ("provider_id", "model", "display_name")},
                                  "attempt_limit": min(attempts, budget)})
                route_chains[task] = chain
            ready.append(path)
            previews.append({"path": path, "routes": connections, "route_chains": route_chains, "budget": budget})
        except ValueError as exc:
            skipped.append({"path": path, "reason": str(exc)})
    options["request_budgets"] = {x["path"]: x["budget"] for x in previews}
    return {"paths": ready, "skipped": skipped, "items": previews, "request_limit": sum(x["budget"] for x in previews), "manual_options": options}
