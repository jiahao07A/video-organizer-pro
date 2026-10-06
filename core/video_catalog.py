# -*- coding: utf-8 -*-
"""Database-backed video catalog queries for large local libraries.

The GUI should use this module for list pages instead of loading the complete
``videos`` table and filtering Python objects on the UI thread.  The catalog is
intentionally independent of Qt; it accepts a DatabaseManager-like object with
``execute_query``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".m4v", ".flv", ".wmv"})
LIST_COLUMNS = (
    "id",
    "path",
    "filename",
    "category",
    "summary",
    "tags",
    "tag_groups",
    "status",
    "thumbnail",
    "timestamp",
)
ORDER_COLUMNS = {
    "id": "id",
    "library_id": "id",
    "filename": "filename",
    "category": "category",
    "status": "status",
    "timestamp": "timestamp",
}
STATUS_ALIASES = {
    "未分析": "pending",
    "pending": "pending",
    "已分析": "analyzed",
    "analyzed": "analyzed",
    "已重命名": "renamed",
    "renamed": "renamed",
}


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _as_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    # PySide6 QDate/QDateTime expose toString but are deliberately not
    # imported here.  Their ISO text is sufficient for SQL date comparison.
    if hasattr(value, "toString"):
        try:
            text = value.toString("yyyy-MM-dd")
        except Exception:
            pass
    return text[:10] or None


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _json_tag_pattern(tag: str) -> str:
    # Tags are stored as a JSON list. Matching the quoted JSON token avoids
    # matching a short tag inside a longer tag in normal cases.
    return f"%{_escape_like(json.dumps(str(tag), ensure_ascii=False))}%"


def _decode_json(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _row_to_list_video(row: Mapping[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    d["tags"] = _decode_json(d.get("tags"), [])
    d["tag_groups"] = _decode_json(d.get("tag_groups"), {})
    d["thumbnail_path"] = d.get("thumbnail")
    d["library_id"] = d.get("id")
    # Keep the same public hard-delete semantics as DatabaseManager.
    d["emotion"] = None
    d["composition"] = None
    d["rating"] = 0
    d["quality_score"] = None
    d["is_proxy_needed"] = 0
    d["tag_weights"] = {}
    return d


@dataclass(frozen=True)
class VideoQuery:
    """Serializable query state shared by list rendering and bulk actions."""

    text: str = ""
    categories: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    only_dup: bool = False
    analysis_statuses: Tuple[str, ...] = ()
    tag_match_mode: str = "any"
    summary_empty: str = "any"
    alias_map: Mapping[str, str] = field(default_factory=dict)
    scope_paths: Optional[Tuple[str, ...]] = None
    excluded_paths: Tuple[str, ...] = ()
    order_by: str = "timestamp"
    descending: bool = True

    @classmethod
    def from_filter_params(
        cls,
        params: Optional[Mapping[str, Any]] = None,
        *,
        scope_paths: Optional[Sequence[str]] = None,
        excluded_paths: Optional[Sequence[str]] = None,
        order_by: str = "timestamp",
        descending: bool = True,
    ) -> "VideoQuery":
        p = params or {}
        statuses: List[str] = []
        for raw in p.get("analysis_statuses") or ():
            status = STATUS_ALIASES.get(str(raw), str(raw).lower())
            statuses.append(status)
        return cls(
            text=_clean_text(p.get("text")),
            categories=tuple(_clean_text(x) for x in p.get("categories") or () if _clean_text(x)),
            tags=tuple(_clean_text(x) for x in p.get("tags") or () if _clean_text(x)),
            date_start=_as_iso(p.get("date_start")),
            date_end=_as_iso(p.get("date_end")),
            only_dup=bool(p.get("only_dup")),
            analysis_statuses=tuple(dict.fromkeys(statuses)),
            tag_match_mode="all" if p.get("tag_match_mode") == "all" else "any",
            summary_empty=str(p.get("summary_empty") or "any"),
            alias_map=dict(p.get("alias_map") or {}),
            scope_paths=tuple(str(x) for x in scope_paths or () if str(x).strip()),
            excluded_paths=tuple(str(x) for x in excluded_paths or () if str(x).strip()),
            order_by=order_by,
            descending=descending,
        )


@dataclass(frozen=True)
class VideoPage:
    rows: List[Dict[str, Any]]
    total_count: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.rows) < self.total_count


class VideoCatalog:
    """Build and execute bounded SQL queries against the video table."""

    def __init__(self, db: Any):
        self.db = db

    def _where(self, query: VideoQuery) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []

        if query.text:
            needle = f"%{_escape_like(query.text.lower())}%"
            clauses.append(
                "LOWER(COALESCE(filename, '') || ' ' || COALESCE(category, '') || ' ' "
                "|| COALESCE(tags, '') || ' ' || COALESCE(summary, '')) LIKE ? ESCAPE '\\'"
            )
            params.append(needle)

        if query.categories:
            placeholders = ", ".join("?" for _ in query.categories)
            clauses.append(f"COALESCE(category, '') IN ({placeholders})")
            params.extend(query.categories)

        if query.analysis_statuses:
            placeholders = ", ".join("?" for _ in query.analysis_statuses)
            clauses.append(
                f"COALESCE(NULLIF(LOWER(status), ''), 'pending') IN ({placeholders})"
            )
            params.extend(query.analysis_statuses)

        if query.summary_empty == "empty":
            clauses.append("TRIM(COALESCE(summary, '')) = ''")
        elif query.summary_empty == "nonempty":
            clauses.append("TRIM(COALESCE(summary, '')) <> ''")

        if query.date_start:
            clauses.append("date(timestamp) >= date(?)")
            params.append(query.date_start)
        if query.date_end:
            clauses.append("date(timestamp) <= date(?)")
            params.append(query.date_end)

        if query.only_dup:
            # The legacy UI exposed this filter but the videos schema has no
            # persisted duplicate flag. Returning no rows is safer than
            # silently treating every item as a duplicate.
            clauses.append("1 = 0")

        if query.tags:
            expanded: List[List[str]] = []
            for tag in query.tags:
                values = [tag]
                standard = query.alias_map.get(tag)
                if standard:
                    values.append(str(standard))
                # Selecting a standard should also match its aliases only when
                # the caller explicitly supplied an alias map expansion.
                values.extend(
                    alias for alias, target in query.alias_map.items() if target == tag
                )
                expanded.append(list(dict.fromkeys(values)))

            def one_tag_clause(values: Sequence[str]) -> Tuple[str, List[str]]:
                parts = ["COALESCE(tags, '') LIKE ? ESCAPE '\\'" for _ in values]
                return "(" + " OR ".join(parts) + ")", [_json_tag_pattern(v) for v in values]

            tag_clauses: List[str] = []
            for values in expanded:
                clause, tag_params = one_tag_clause(values)
                tag_clauses.append(clause)
                params.extend(tag_params)
            joiner = " AND " if query.tag_match_mode == "all" else " OR "
            clauses.append("(" + joiner.join(tag_clauses) + ")")

        scope_clause, scope_params = self._scope_clause(query.scope_paths)
        if scope_clause:
            clauses.append(scope_clause)
            params.extend(scope_params)

        excluded = [_path_key(p) for p in query.excluded_paths]
        if excluded:
            placeholders = ", ".join("?" for _ in excluded)
            # Paths are normalized when they are registered. The OR form
            # also preserves compatibility with older rows on case-sensitive
            # filesystems.
            clauses.append(f"path NOT IN ({placeholders})")
            params.extend(excluded)

        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    @staticmethod
    def _scope_clause(scope_paths: Sequence[str]) -> Tuple[str, List[str]]:
        clauses: List[str] = []
        params: List[str] = []
        for raw in scope_paths or ():
            path = str(raw).strip()
            if not path:
                continue
            absolute = os.path.abspath(path)
            if os.path.isfile(path):
                clauses.append("path = ?")
                params.append(absolute)
                continue
            if os.path.isdir(path):
                prefix = absolute.rstrip(os.sep) + os.sep
                clauses.append("(path = ? OR path LIKE ? ESCAPE '\\')")
                params.extend([absolute, _escape_like(prefix) + "%"])
                continue
            _, ext = os.path.splitext(path)
            if ext.lower() in VIDEO_EXTENSIONS:
                clauses.append("path = ?")
                params.append(absolute)
            else:
                prefix = absolute.rstrip(os.sep) + os.sep
                clauses.append("(path = ? OR path LIKE ? ESCAPE '\\')")
                params.extend([absolute, _escape_like(prefix) + "%"])
        if not clauses:
            return "", []
        return "(" + " OR ".join(clauses) + ")", params

    def _order(self, query: VideoQuery) -> str:
        column = ORDER_COLUMNS.get(query.order_by, "timestamp")
        direction = "DESC" if query.descending else "ASC"
        # The stable id tie-breaker makes list numbers deterministic.
        tie_direction = direction
        return f"{column} {direction}, id {tie_direction}"

    def count(self, query: Optional[VideoQuery] = None) -> int:
        q = query or VideoQuery()
        where, params = self._where(q)
        rows = self.db.execute_query(f"SELECT COUNT(*) AS n FROM videos{where}", tuple(params))
        return int(rows[0]["n"] if rows else 0)

    def list_page(
        self,
        query: Optional[VideoQuery] = None,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> VideoPage:
        q = query or VideoQuery()
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 2000))
        where, params = self._where(q)
        columns = ", ".join(LIST_COLUMNS)
        rows = self.db.execute_query(
            f"SELECT {columns}, COUNT(*) OVER() AS _total_count "
            f"FROM videos{where} ORDER BY {self._order(q)} LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        )
        total_count = int(rows[0].get("_total_count") or 0) if rows else self.count(q)
        projected_rows = []
        for row in rows:
            data = dict(row)
            data.pop("_total_count", None)
            projected_rows.append(_row_to_list_video(data))
        return VideoPage(
            rows=projected_rows,
            total_count=total_count,
            offset=offset,
            limit=limit,
        )

    def resolve_visible_paths(self, query: Optional[VideoQuery] = None) -> List[str]:
        """Return every path matching a query, independent of UI pagination."""
        q = query or VideoQuery()
        where, params = self._where(q)
        rows = self.db.execute_query(
            f"SELECT path FROM videos{where} ORDER BY {self._order(q)}", tuple(params)
        )
        return [str(row["path"]) for row in rows if row.get("path")]

    def get_detail(self, path: str) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_query("SELECT * FROM videos WHERE path = ? LIMIT 1", (path,))
        if not rows:
            return None
        row = dict(rows[0])
        row["tags"] = _decode_json(row.get("tags"), [])
        row["raw_metadata"] = _decode_json(row.get("raw_metadata"), {})
        row["face_clusters"] = _decode_json(row.get("face_clusters"), [])
        row["tag_groups"] = _decode_json(row.get("tag_groups"), {})
        row["thumbnail_path"] = row.get("thumbnail")
        row["library_id"] = row.get("id")
        row["emotion"] = None
        row["composition"] = None
        row["rating"] = 0
        row["quality_score"] = None
        row["is_proxy_needed"] = 0
        row["tag_weights"] = {}
        return row
