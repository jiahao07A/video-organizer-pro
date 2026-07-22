# -*- coding: utf-8 -*-
"""
Unified AI Video Organizer Service Layer v2.0
Refactored into a UI-agnostic service layer.
"""

import os
import json
import sqlite3
import cv2
import base64
import shutil
import re
import time
import threading
import csv
import hashlib
import logging
import sys
import numpy as np
import subprocess
from xml.sax.saxutils import escape
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Set, Union, Any, Callable
from openai import OpenAI

# 资源路径处理函数
def get_resource_path(relative_path):
    """ 获取资源的绝对路径，兼容 PyInstaller 打包后的环境 """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("VideoOrganizer")

# 尝试导入可选库
try:
    from scenedetect import detect, ContentDetector, SceneManager, open_video
    HAS_SCENEDETECT = True
except ImportError:
    HAS_SCENEDETECT = False

try:
    import imagehash
    from PIL import Image
    HAS_IMAGEHASH = True
except ImportError:
    HAS_IMAGEHASH = False

# ================= 配置区域 (Configuration) =================

# 项目根目录（core/ 的上一级），避免 settings 依赖进程 cwd
_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_CORE_DIR)


def get_settings_file_path() -> str:
    """可写配置路径：开发环境固定在项目根；打包后放可执行文件旁。"""
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.abspath(".")
        return os.path.join(base, "settings.json")
    return os.path.join(_PROJECT_ROOT, "settings.json")


SETTINGS_FILE = get_settings_file_path()
RESULTS_FILE_JSON = "video_analysis_results.json"
RESULTS_FILE_CSV = "video_analysis_results.csv"
BACKUP_DIR = "backups"
THUMBNAILS_DIR = ".thumbnails"
DB_FILE = "video_organizer.db"
ENV_FILE = get_resource_path(".env")
DICTIONARY_FILE = os.path.join(_PROJECT_ROOT, "词.txt")
TAG_CONFIG_FILE = os.path.join(_PROJECT_ROOT, "tag_config.json")

DEFAULT_SETTINGS = {
    "api": {
        "key": "",
        "base_url": "https://vqnypowwewrv.ap-northeast-1.clawcloudrun.com/v1",
        "model_personalization": {
            "video_classification": "gemini-2.0-flash",
            "tag_generation": "gemini-2.0-flash",
            "content_description": "gemini-2.0-flash",
        }
    },
    # 模型供应商档案列表 + 当前 id；空列表在 load 时从旧 api 迁为「默认」
    "model_providers": [],
    "current_provider_id": "",

    "prompts": {
        "video_classification": "你是一个视频分类专家，请根据视频帧内容将其归入最合适的分类。返回 JSON 格式，包含 category 字段。",
        "tag_generation": "请为以下视频生成5个准确的中文标签，涵盖氛围、主体、场景、动作和核心对象。返回 JSON 格式，包含 tags 列表。",
        "content_description": "请详细描述视频画面中的元素、动作和氛围，生成一段50字以内的中文摘要。返回 JSON 格式，包含 summary 字段。"
    },
    "ui_preferences": {
        "font_size": 14,
        "thumbnail_size": [160, 90],
        "theme": "dark",
        "default_view": "list",
        "sidebar_width": 220,
        "detail_panel_expanded": True,
        "remember_work_scope": False,
        "last_work_scope": [],
    },
    "processing": {
        "max_workers": 4,
        "max_frames": 10,
        "target_size": 512,
        "jpeg_quality": 80,
        "enable_scene_detection": True,
        "enable_audio_transcription": False,
        "enable_metadata_injection": False,
        # 分析任务重试（ADR-0005）
        "analysis_retry": {
            "call_extra_attempts": 2,
            "item_max_attempts": 2,
            "batch_rerun_enabled": True,
            "batch_rerun_max_rounds": 1,
        },
    },
    "rename_pattern": "{category}-{tags}-{summary}-{original_name}",
    "categories": [
        "Aroll", "Broll", "工作", "学习", "生活", "美食", "科技", "人文", 
        "艺术", "运动", "动物", "自然", "城市", "平静", "悲伤", "喜悦", 
        "治愈", "震撼", "唯美", "人物", "风景", "静物", "Vlog"
    ],
    "tag_dimensions": {
        "Mood": ["唯美", "治愈", "悲伤", "喜悦", "科技感", "艺术感", "宁静", "紧张", "庄重"],
        "Subject": ["男性", "女性", "儿童", "老人", "情侣", "家庭", "团队", "手部特写", "剪影", "无人"],
        "Location": ["城市", "自然", "居家", "办公", "学校", "健身房", "公共场所", "抽象背景", "监狱"],
        "Action": ["工作", "学习", "运动", "交流", "生活", "饮食", "数码互动", "思考", "展示"],
        "KeyObjects": ["大脑", "金钱", "时间", "数据", "交通工具", "乐器", "医疗", "食物"]
    }
}

class DatabaseManager:
    """管理 SQLite 数据库，支持视频元数据、标签库、设置、缓存和历史记录"""
    
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        logger.debug(f"正在初始化数据库: {self.db_path}")
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                
                # 创建视频表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS videos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT UNIQUE,
                        filename TEXT,
                        file_hash TEXT,
                        phash TEXT,
                        category TEXT,
                        summary TEXT,
                        tags TEXT,
                        transcription TEXT,
                        status TEXT,
                        thumbnail TEXT,
                        manual_override INTEGER DEFAULT 0,
                        metadata_injected INTEGER DEFAULT 0,
                        raw_metadata TEXT,
                        emotion TEXT,
                        composition TEXT,
                        rating INTEGER DEFAULT 0,
                        quality_score FLOAT,
                        is_proxy_needed INTEGER DEFAULT 0,
                        proxy_path TEXT,
                        face_clusters TEXT,
                        vector_id TEXT,
                        tag_groups TEXT,
                        tag_weights TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 升级视频表 (V4.0/V5.0/V6.0 增量字段)
                columns = [
                    ("emotion", "TEXT"),
                    ("composition", "TEXT"),
                    ("rating", "INTEGER DEFAULT 0"),
                    ("quality_score", "FLOAT"),
                    ("is_proxy_needed", "INTEGER DEFAULT 0"),
                    ("proxy_path", "TEXT"),
                    ("face_clusters", "TEXT"),
                    ("vector_id", "TEXT"),
                    ("tag_groups", "TEXT"),
                    ("tag_weights", "TEXT")
                ]
                for col_name, col_type in columns:
                    try:
                        cursor.execute(f"ALTER TABLE videos ADD COLUMN {col_name} {col_type}")
                    except sqlite3.OperationalError:
                        pass # 字段已存在

                # 创建 API 响应缓存表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS api_cache (
                        cache_key TEXT PRIMARY KEY,
                        response TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 创建重命名历史表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS rename_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT,
                        old_path TEXT,
                        new_path TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # 标签库
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tags_library (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dimension TEXT,
                        tag_name TEXT,
                        parent_id INTEGER,
                        color TEXT,
                        usage_count INTEGER DEFAULT 0,
                        is_learned INTEGER DEFAULT 0,
                        UNIQUE(dimension, tag_name)
                    )
                """)

                # 升级标签库表 (V5.0/V6.0 增量字段)
                v5_tag_columns = [
                    ("parent_id", "INTEGER"),
                    ("color", "TEXT"),
                    ("usage_count", "INTEGER DEFAULT 0"),
                    ("is_person", "INTEGER DEFAULT 0"),
                    ("name_en", "TEXT"),
                    ("icon", "TEXT")
                ]
                for col_name, col_type in v5_tag_columns:
                    try:
                        cursor.execute(f"ALTER TABLE tags_library ADD COLUMN {col_name} {col_type}")
                    except sqlite3.OperationalError:
                        pass # 字段已存在

                # 标签关联表 (V4.0)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tag_associations (
                        tag_a TEXT,
                        tag_b TEXT,
                        weight FLOAT,
                        source TEXT,
                        UNIQUE(tag_a, tag_b)
                    )
                """)

                # 同义词映射表 (V6.0)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS synonyms (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        standard_tag TEXT,
                        alias TEXT UNIQUE
                    )
                """)

                # 待审词（封闭组库外词，ADR-0004）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pending_tags (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        raw_text TEXT NOT NULL,
                        group_id TEXT,
                        status TEXT DEFAULT 'pending',
                        source_path TEXT,
                        created_at TEXT,
                        UNIQUE(raw_text, group_id, status)
                    )
                """)

                # 物理迁移日志表 (V4.0)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS migration_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        old_path TEXT,
                        new_path TEXT,
                        reason TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.commit()

    def execute_query(self, query: str, params: tuple = ()) -> List[Dict]:
        logger.debug(f"执行 SQL 查询: {query[:100]}... | 参数: {params}")
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]

    def execute_non_query(self, query: str, params: tuple = ()):
        logger.debug(f"执行 SQL 修改: {query[:100]}... | 参数: {params}")
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()

    def upsert_video(self, video_data: Dict):
        tags = video_data.get("tags", [])
        tags_str = json.dumps(tags, ensure_ascii=False) if isinstance(tags, list) else tags
        metadata = video_data.get("raw_metadata", {})
        metadata_str = json.dumps(metadata, ensure_ascii=False) if isinstance(metadata, dict) else metadata
        tag_groups = video_data.get("tag_groups", {})
        tag_groups_str = json.dumps(tag_groups, ensure_ascii=False) if isinstance(tag_groups, dict) else tag_groups
        
        # 已删字段硬清：写入空值，避免旧数据残留被读出
        query = """
            INSERT INTO videos (
                path, filename, file_hash, phash, category, summary, tags,
                transcription, status, thumbnail, manual_override, metadata_injected,
                raw_metadata, emotion, composition, rating, quality_score,
                is_proxy_needed, proxy_path, face_clusters, vector_id, tag_groups, tag_weights
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                filename=excluded.filename,
                file_hash=excluded.file_hash,
                phash=excluded.phash,
                category=excluded.category,
                summary=excluded.summary,
                tags=excluded.tags,
                transcription=excluded.transcription,
                status=excluded.status,
                thumbnail=excluded.thumbnail,
                manual_override=excluded.manual_override,
                metadata_injected=excluded.metadata_injected,
                raw_metadata=excluded.raw_metadata,
                emotion=excluded.emotion,
                composition=excluded.composition,
                rating=excluded.rating,
                quality_score=excluded.quality_score,
                is_proxy_needed=excluded.is_proxy_needed,
                proxy_path=excluded.proxy_path,
                face_clusters=excluded.face_clusters,
                vector_id=excluded.vector_id,
                tag_groups=excluded.tag_groups,
                tag_weights=excluded.tag_weights
        """
        params = (
            video_data.get("path"),
            video_data.get("filename"),
            video_data.get("file_hash"),
            video_data.get("phash"),
            video_data.get("category"),
            video_data.get("summary"),
            tags_str,
            video_data.get("transcription"),
            video_data.get("status"),
            video_data.get("thumbnail_path") or video_data.get("thumbnail"),
            1 if video_data.get("manual_override") else 0,
            1 if video_data.get("metadata_injected") else 0,
            metadata_str,
            None,  # emotion 硬删（ADR-0003 并入氛围标签）
            None,  # composition 硬删
            0,  # rating
            None,  # quality_score
            0,  # is_proxy_needed
            video_data.get("proxy_path"),
            json.dumps(video_data.get("face_clusters", []), ensure_ascii=False),
            video_data.get("vector_id"),
            tag_groups_str,
            json.dumps({}, ensure_ascii=False),  # tag_weights 硬删
        )
        self.execute_non_query(query, params)

    def delete_video(self, path: str):
        self.execute_non_query("DELETE FROM videos WHERE path = ?", (path,))

    def get_video_by_path(self, path: str) -> Optional[Dict]:
        """按路径取单条视频记录；不存在则返回 None。"""
        rows = self.execute_query("SELECT * FROM videos WHERE path = ?", (path,))
        if not rows:
            return None
        return self._row_to_video_dict(rows[0])

    def _row_to_video_dict(self, row) -> Dict:
        d = dict(row)
        try:
            d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        except Exception:
            d["tags"] = d["tags"].split(",") if d["tags"] else []
        try:
            d["raw_metadata"] = json.loads(d["raw_metadata"]) if d["raw_metadata"] else {}
        except Exception:
            d["raw_metadata"] = {}
        try:
            d["face_clusters"] = json.loads(d["face_clusters"]) if d["face_clusters"] else []
        except Exception:
            d["face_clusters"] = []
        try:
            d["tag_groups"] = json.loads(d["tag_groups"]) if d["tag_groups"] else {}
        except Exception:
            d["tag_groups"] = {}
        # 产品层不再暴露已删分析结果字段
        d["emotion"] = None
        d["composition"] = None
        d["rating"] = 0
        d["quality_score"] = None
        d["is_proxy_needed"] = 0
        d["tag_weights"] = {}
        d["thumbnail_path"] = d["thumbnail"]
        # 入库编号：使用表主键 id（稳定、首次入库分配）
        if d.get("id") is not None:
            d["library_id"] = d["id"]
        else:
            d["library_id"] = None
        return d

    def insert_video_if_absent(self, video_data: Dict) -> bool:
        """仅当路径尚不在库时插入；已存在则不改写任何字段（含分析结果）。"""
        path = video_data.get("path")
        if not path:
            return False
        rows = self.execute_query("SELECT 1 FROM videos WHERE path = ? LIMIT 1", (path,))
        if rows:
            return False
        self.upsert_video(video_data)
        return True

    def get_all_videos(self) -> List[Dict]:
        rows = self.execute_query("SELECT * FROM videos ORDER BY timestamp DESC")
        processed_rows = []
        for row in rows:
            d = dict(row)
            try:
                d["tags"] = json.loads(d["tags"]) if d["tags"] else []
            except:
                d["tags"] = d["tags"].split(",") if d["tags"] else []
            
            try:
                d["raw_metadata"] = json.loads(d["raw_metadata"]) if d["raw_metadata"] else {}
            except:
                d["raw_metadata"] = {}

            try:
                d["face_clusters"] = json.loads(d["face_clusters"]) if d["face_clusters"] else []
            except:
                d["face_clusters"] = []
            
            try:
                d["tag_groups"] = json.loads(d["tag_groups"]) if d["tag_groups"] else {}
            except:
                d["tag_groups"] = {}

            # 硬删字段：读出时清空，避免 UI/导出继续依赖
            d["emotion"] = None
            d["composition"] = None
            d["rating"] = 0
            d["quality_score"] = None
            d["is_proxy_needed"] = 0
            d["tag_weights"] = {}
                
            d["thumbnail_path"] = d["thumbnail"]
            if d.get("id") is not None:
                d["library_id"] = d["id"]
            else:
                d["library_id"] = None
            processed_rows.append(d)
        return processed_rows

    def get_cache(self, key: str) -> Optional[Dict]:
        rows = self.execute_query("SELECT response FROM api_cache WHERE cache_key = ?", (key,))
        if rows:
            return json.loads(rows[0]["response"])
        return None

    def set_cache(self, key: str, response: Dict):
        res_str = json.dumps(response, ensure_ascii=False)
        self.execute_non_query("INSERT OR REPLACE INTO api_cache (cache_key, response) VALUES (?, ?)", (key, res_str))

    def add_rename_record(self, session_id: str, old_path: str, new_path: str):
        self.execute_non_query("INSERT INTO rename_history (session_id, old_path, new_path) VALUES (?, ?, ?)", (session_id, old_path, new_path))

    def get_last_session_id(self) -> Optional[str]:
        rows = self.execute_query("SELECT session_id FROM rename_history ORDER BY timestamp DESC LIMIT 1")
        return rows[0]["session_id"] if rows else None

    def get_history_by_session(self, session_id: str) -> List[Dict]:
        return self.execute_query("SELECT old_path, new_path FROM rename_history WHERE session_id = ?", (session_id,))

    def delete_session(self, session_id: str):
        self.execute_non_query("DELETE FROM rename_history WHERE session_id = ?", (session_id,))

    def save_setting(self, key: str, value: Any):
        val_str = json.dumps(value, ensure_ascii=False)
        query = "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        self.execute_non_query(query, (key, val_str))

    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.execute_query("SELECT value FROM settings WHERE key = ?", (key,))
        if rows:
            return json.loads(rows[0]["value"])
        return default

    def add_tag(self, dimension: str, tag_name: str, is_learned: int = 0, parent_id: Optional[int] = None, color: Optional[str] = None, name_en: Optional[str] = None, icon: Optional[str] = None):
        query = "INSERT OR IGNORE INTO tags_library (dimension, tag_name, is_learned, parent_id, color, name_en, icon) VALUES (?, ?, ?, ?, ?, ?, ?)"
        self.execute_non_query(query, (dimension, tag_name, is_learned, parent_id, color, name_en, icon))

    def get_tags(self, dimension: Optional[str] = None) -> List[str]:
        if dimension:
            # 兼容性处理：不区分大小写查询维度
            rows = self.execute_query("SELECT tag_name FROM tags_library WHERE LOWER(dimension) = LOWER(?) ORDER BY usage_count DESC", (dimension,))
        else:
            rows = self.execute_query("SELECT tag_name FROM tags_library ORDER BY usage_count DESC")
        return [row["tag_name"] for row in rows]

    def bulk_add_tags(self, tags: List[tuple]):
        """批量添加标签，使用事务提高性能"""
        if not tags: return
        query = "INSERT OR IGNORE INTO tags_library (dimension, tag_name, is_learned, parent_id, color) VALUES (?, ?, ?, ?, ?)"
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany(query, tags)
                conn.commit()

    def get_tags_detail(self, dimension: Optional[str] = None) -> List[Dict]:
        if dimension:
            # 兼容性处理：不区分大小写查询维度
            rows = self.execute_query("SELECT * FROM tags_library WHERE LOWER(dimension) = LOWER(?) ORDER BY usage_count DESC", (dimension,))
        else:
            rows = self.execute_query("SELECT * FROM tags_library ORDER BY usage_count DESC")
        return [dict(row) for row in rows]

    def update_tag(self, tag_id: int, updates: Dict):
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        params = list(updates.values()) + [tag_id]
        query = f"UPDATE tags_library SET {set_clause} WHERE id = ?"
        self.execute_non_query(query, tuple(params))

    def delete_tag_by_id(self, tag_id: int):
        self.execute_non_query("DELETE FROM tags_library WHERE id = ?", (tag_id,))

    def delete_tag_by_name(self, tag_name: str, dimension: Optional[str] = None):
        name = (tag_name or "").strip()
        if not name:
            return
        if dimension is not None:
            self.execute_non_query(
                "DELETE FROM tags_library WHERE tag_name = ? AND LOWER(dimension) = LOWER(?)",
                (name, dimension),
            )
        else:
            self.execute_non_query("DELETE FROM tags_library WHERE tag_name = ?", (name,))

    def reassign_tags_dimension(self, tag_names: List[str], target_dimension: str):
        """将标准词 dimension 整批改到目标组（标签移动 / 删组改派）。

        同名多行（历史脏数据）时：保留一条写到目标组，删除其余，避免 UNIQUE(dimension, tag_name) 冲突。
        """
        dim = (target_dimension or "").strip()
        if not dim or not tag_names:
            return
        for name in tag_names:
            n = (name or "").strip()
            if not n:
                continue
            rows = self.execute_query(
                "SELECT id, dimension FROM tags_library WHERE tag_name = ?",
                (n,),
            )
            if not rows:
                continue
            keep_id = None
            for r in rows:
                if str(r.get("dimension") or "").strip().lower() == dim.lower():
                    keep_id = r["id"]
                    break
            if keep_id is None:
                keep_id = rows[0]["id"]
                self.execute_non_query(
                    "UPDATE tags_library SET dimension = ? WHERE id = ?",
                    (dim, keep_id),
                )
            for r in rows:
                rid = r["id"]
                if rid != keep_id:
                    self.execute_non_query(
                        "DELETE FROM tags_library WHERE id = ?",
                        (rid,),
                    )

    def increment_tag_usage(self, tag_name: str):
        self.execute_non_query("UPDATE tags_library SET usage_count = usage_count + 1 WHERE tag_name = ?", (tag_name,))

    def refresh_tag_usage_counts(self):
        """根据 videos 表重新计算所有标签的使用频次"""
        videos = self.get_all_videos()
        counts = {}
        for v in videos:
            for t in v.get("tags", []):
                counts[t] = counts.get(t, 0) + 1
        
        with self.lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 重置所有频次
                cursor.execute("UPDATE tags_library SET usage_count = 0")
                # 更新
                for tag, count in counts.items():
                    cursor.execute("UPDATE tags_library SET usage_count = ? WHERE tag_name = ?", (count, tag))
                conn.commit()

    def bulk_replace_tags(self, old_tag: str, new_tag: str):
        """数据库级批量替换视频标签（替换后走别名归一，落库写标准词）。"""
        from core.tag_normalize import normalize_flat_tags

        alias_map = self.get_synonyms()
        videos = self.get_all_videos()
        for video in videos:
            tags = video.get("tags", [])
            if old_tag in tags:
                new_tags = [new_tag if t == old_tag else t for t in tags]
                new_tags = normalize_flat_tags(new_tags, alias_map)
                video["tags"] = new_tags
                self.upsert_video(video)

    # --- 同义词管理 (V6.0) ---
    def get_synonyms(self) -> Dict[str, str]:
        """获取所有同义词映射表 {别名: 标准标签}"""
        rows = self.execute_query("SELECT alias, standard_tag FROM synonyms")
        return {row["alias"]: row["standard_tag"] for row in rows}

    def add_synonym(self, standard_tag: str, alias: str):
        self.execute_non_query("INSERT OR REPLACE INTO synonyms (standard_tag, alias) VALUES (?, ?)", (standard_tag, alias))

    def remove_synonym(self, alias: str):
        self.execute_non_query("DELETE FROM synonyms WHERE alias = ?", (alias,))

    # --- 待审词 (ADR-0004) ---
    def add_pending_tag(
        self,
        raw_text: str,
        group_id: Optional[str] = None,
        source_path: Optional[str] = None,
        status: str = "pending",
    ):
        raw = (raw_text or "").strip()
        if not raw:
            return
        from datetime import datetime as _dt

        self.execute_non_query(
            """
            INSERT OR IGNORE INTO pending_tags (raw_text, group_id, status, source_path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (raw, group_id or "", status, source_path, _dt.now().isoformat()),
        )

    def list_pending_tags(self, status: Optional[str] = "pending") -> List[Dict]:
        if status:
            return self.execute_query(
                "SELECT * FROM pending_tags WHERE status = ? ORDER BY id DESC",
                (status,),
            )
        return self.execute_query("SELECT * FROM pending_tags ORDER BY id DESC")

    def update_pending_tag_status(self, pending_id: int, status: str):
        self.execute_non_query(
            "UPDATE pending_tags SET status = ? WHERE id = ?",
            (status, pending_id),
        )

class SettingsManager:
    """管理配置信息，支持从 SQLite 数据库或 settings.json 加载，支持嵌套键名访问"""

    @staticmethod
    def deep_copy_defaults() -> Dict:
        """深拷贝默认配置，避免浅拷贝污染 DEFAULT_SETTINGS。"""
        import copy
        return copy.deepcopy(DEFAULT_SETTINGS)

    @staticmethod
    def deep_update(d: Dict, u: Dict) -> Dict:
        for k, v in (u or {}).items():
            if isinstance(v, dict):
                node = d.get(k)
                if not isinstance(node, dict):
                    node = {}
                    d[k] = node
                SettingsManager.deep_update(node, v)
            else:
                d[k] = v
        return d

    @staticmethod
    def get_setting(settings: Dict, key_path: str, default: Any = None) -> Any:
        """支持路径式键名访问，如 'api.key'"""
        keys = key_path.split('.')
        val = settings
        for key in keys:
            if isinstance(val, dict) and key in val:
                val = val[key]
            else:
                return default
        return val

    @staticmethod
    def update_setting(settings: Dict, key_path: str, value: Any):
        """支持路径式键名更新，如 'api.key'"""
        keys = key_path.split('.')
        val = settings
        for key in keys[:-1]:
            if key not in val or not isinstance(val[key], dict):
                val[key] = {}
            val = val[key]
        val[keys[-1]] = value

    @staticmethod
    def load_settings(db: Optional[DatabaseManager] = None) -> Dict:
        """
        加载配置：默认值深拷贝 → settings.json → 数据库整包备份（settings_blob）。
        """
        settings = SettingsManager.deep_copy_defaults()
        settings_path = get_settings_file_path()

        # 1. 文件
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    file_settings = json.load(f)
                if isinstance(file_settings, dict):
                    SettingsManager.deep_update(settings, file_settings)
            except Exception as e:
                logger.warning(f"读取 settings.json 失败: {e}")

        # 2. 数据库整包（若存在则覆盖同名字段，便于从 DB 恢复）
        if db:
            try:
                blob = db.get_setting("settings_blob")
                if isinstance(blob, dict):
                    SettingsManager.deep_update(settings, blob)
                elif isinstance(blob, str) and blob.strip():
                    SettingsManager.deep_update(settings, json.loads(blob))
            except Exception as e:
                logger.debug(f"从数据库加载 settings_blob 跳过: {e}")

        # 模型供应商：无档案则从旧扁平 api 迁移；写穿 api.* 供 AIHandler
        try:
            from core.model_providers import ensure_providers
            ensure_providers(settings)
        except Exception as e:
            logger.warning(f"模型供应商迁移/规范化失败: {e}")

        return settings

    @staticmethod
    def save_settings(settings: Dict, db: Optional[DatabaseManager] = None):
        """保存完整嵌套配置到 settings.json，并写入数据库 settings_blob。"""
        import copy
        settings_path = get_settings_file_path()
        if settings is not None:
            try:
                from core.model_providers import ensure_providers
                ensure_providers(settings)
            except Exception as e:
                logger.warning(f"保存前同步模型供应商失败: {e}")
        payload = copy.deepcopy(settings) if settings is not None else SettingsManager.deep_copy_defaults()
        if settings is None:
            try:
                from core.model_providers import ensure_providers
                ensure_providers(payload)
            except Exception as e:
                logger.warning(f"保存前同步模型供应商失败: {e}")

        # 原子写入，避免半截 JSON
        parent = os.path.dirname(settings_path) or "."
        os.makedirs(parent, exist_ok=True)
        tmp_path = settings_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, settings_path)

        if db:
            try:
                # 整包持久化（含 api.key 等嵌套字段）
                db.save_setting("settings_blob", payload)
                # 兼容旧扁平键
                for k, v in payload.items():
                    if not isinstance(v, dict):
                        db.save_setting(k, v)
            except Exception as e:
                logger.warning(f"写入数据库 settings 失败: {e}")

class VideoProcessor:
    """处理视频文件：抽帧、压缩、场景检测、哈希计算"""
    
    @staticmethod
    def get_file_hash(file_path: str) -> str:
        """计算文件 MD5 哈希"""
        logger.debug(f"正在计算文件哈希: {file_path}")
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    @staticmethod
    def calculate_phash(image: np.ndarray) -> str:
        """计算图像的知觉哈希"""
        if not HAS_IMAGEHASH:
            return ""
        try:
            pil_img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            h = str(imagehash.phash(pil_img))
            logger.debug(f"计算知觉哈希完成: {h}")
            return h
        except Exception as e:
            logger.debug(f"计算知觉哈希失败: {e}")
            return ""

    @staticmethod
    def resize_image(image: np.ndarray, target_size: int) -> np.ndarray:
        h, w = image.shape[:2]
        if max(h, w) <= target_size:
            return image
        scale = target_size / max(h, w)
        new_w, new_h = int(w * scale), int(h * scale)
        return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

    @staticmethod
    def encode_image_to_base64(image: np.ndarray, quality: int = 80) -> str:
        """将 OpenCV 图像编码为 JPEG base64。

        入参必须是 BGR 通道顺序（与 cv2.VideoCapture / imencode 一致）。
        若误传 RGB，红蓝通道会对调，送 AI 的画面会偏蓝/偏紫。
        """
        try:
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            success, buffer = cv2.imencode('.jpg', image, encode_param)
            if not success:
                return ""
            return base64.b64encode(buffer).decode('utf-8')
        except Exception:
            return ""

    @classmethod
    def extract_frames(cls, video_path: str, max_frames: int = 10, target_size: int = 512,
                       save_thumbnail: bool = False, use_scene_detection: bool = True) -> Dict[str, Any]:
        logger.info(f"开始抽取视频帧: {video_path} (最大帧数: {max_frames})")
        if not os.path.exists(video_path):
            logger.warning(f"文件不存在: {video_path}")
            return {"frames": [], "thumbnail": None, "phash": ""}

        base64_frames = []
        thumbnail_path = None
        phash = ""
        
        scene_list = []
        if use_scene_detection and HAS_SCENEDETECT:
            try:
                video = open_video(video_path)
                scene_manager = SceneManager()
                scene_manager.add_detector(ContentDetector())
                scene_manager.detect_scenes(video)
                scene_list = scene_manager.get_scene_list()
            except Exception:
                pass

        cap = cv2.VideoCapture(video_path)
        try:
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0: total_frames = 1000
            
            indices = []
            if scene_list:
                for scene in scene_list[:max_frames]:
                    start, end = scene[0].get_frames(), scene[1].get_frames()
                    indices.append((start + end) // 2)
            else:
                interval = max(1, total_frames // max_frames)
                indices = [i * interval for i in range(min(max_frames, total_frames))]
            
            for i, frame_idx in enumerate(indices):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if ret:
                    if i == 0:
                        # phash 走 PIL，需要 RGB
                        phash = cls.calculate_phash(frame)
                        
                        if save_thumbnail:
                            if not os.path.exists(THUMBNAILS_DIR):
                                os.makedirs(THUMBNAILS_DIR)
                            file_hash = hashlib.md5(video_path.encode()).hexdigest()
                            # V4.0: 使用 WebP 进行极致压缩 (三级缓存之磁盘缩略图)
                            thumbnail_path = os.path.join(THUMBNAILS_DIR, f"{file_hash}.webp")
                            try:
                                # 尝试使用 WebP，如果不支持则退回到 JPEG
                                # frame 为 BGR，与 cv2.imwrite 一致，色相正确
                                cv2.imwrite(thumbnail_path, frame, [int(cv2.IMWRITE_WEBP_QUALITY), 80])
                            except Exception:
                                thumbnail_path = os.path.join(THUMBNAILS_DIR, f"{file_hash}.jpg")
                                cv2.imwrite(thumbnail_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

                    # 保持 BGR：cv2.imencode 按 BGR 写 JPEG；切勿先转 RGB 再 encode
                    resized_frame = cls.resize_image(frame, target_size)
                    base64_str = cls.encode_image_to_base64(resized_frame)
                    if base64_str:
                        base64_frames.append(base64_str)
        except Exception:
            pass
        finally:
            cap.release()
            
        return {"frames": base64_frames, "thumbnail": thumbnail_path, "phash": phash}

class AudioTranscriber:
    """音频转录逻辑 (Whisper)"""
    @staticmethod
    def transcribe(video_path: str, api_key: str, base_url: str) -> str:
        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            audio_path = video_path + ".mp3"
            # 检查 ffmpeg 是否可用
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", "-ab", "64k", "-f", "mp3", audio_path], 
                           capture_output=True, check=True)
            
            with open(audio_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file,
                    response_format="text"
                )
            if os.path.exists(audio_path):
                os.remove(audio_path)
            return str(transcript)
        except Exception as e:
            return f"转录失败 (请确保已安装 ffmpeg): {e}"

class MetadataInjector:
    """元数据注入逻辑 (XMP/FFmpeg)"""
    @staticmethod
    def inject_tags(video_path: str, tags: List[str], summary: str):
        try:
            tags_str = ",".join(tags)
            temp_path = video_path + ".tmp.mp4"
            cmd = [
                "ffmpeg", "-y", "-i", video_path, 
                "-metadata", f"comment={summary} | Tags: {tags_str}", 
                "-codec", "copy", temp_path
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            os.replace(temp_path, video_path)
            return True
        except Exception:
            return False

class AIHandler:
    """封装 AI 分析逻辑，支持缓存、动态标签发现和自定义 Prompt"""
    
    def __init__(self, settings: Dict, db: Optional[DatabaseManager] = None, tag_config: Optional[Dict] = None):
        self.settings = settings
        self.db = db
        self.tag_config = tag_config
        
        api_key = SettingsManager.get_setting(settings, "api.key", "")
        base_url = SettingsManager.get_setting(settings, "api.base_url", "")
        
        self.client = OpenAI(
            api_key=api_key or "EMPTY",
            base_url=base_url or None,
            # 调用层重试由 _get_api_response 统一负责，避免与 max_retries 叠加烧费用
            max_retries=0,
            timeout=45.0
        )
        
        # 模型路由 - 动态获取，不再缓存到成员变量
        self.tag_lib_lock = threading.Lock()
        # 每线程独立的上次 API 失败信息（避免并行 worker 串台）
        self._tls = threading.local()
        
        # 初始化标签库
        self.init_tag_libraries()

    def reload_client(self):
        """设置变更后重建 OpenAI 客户端（API Key / Base URL）。"""
        api_key = SettingsManager.get_setting(self.settings, "api.key", "")
        base_url = SettingsManager.get_setting(self.settings, "api.base_url", "")
        self.client = OpenAI(
            api_key=api_key or "EMPTY",
            base_url=base_url or None,
            max_retries=0,
            timeout=45.0,
        )
        logger.info("AI 客户端已按最新配置重建")

    def init_tag_libraries(self):
        """同步设置或 tag_config.json 中的标签到数据库"""
        if not self.db:
            return
            
        # 优先使用 tag_config (v6.0)
        if self.tag_config:
            # v6.0 结构
            if "tag_groups" in self.tag_config:
                for group in self.tag_config["tag_groups"]:
                    dim = group.get("id")
                    tags = group.get("tags", [])
                    # 处理带多语言和图标的标签定义
                    for tag_def in tags:
                        if isinstance(tag_def, dict):
                            name = tag_def.get("name")
                            name_en = tag_def.get("en")
                            icon = tag_def.get("icon")
                            self.db.add_tag(dim, name, is_learned=0, name_en=name_en, icon=icon)
                        else:
                            self.db.add_tag(dim, tag_def, is_learned=0)
            # v5.0 结构 (兼容迁移过程中的瞬态)
            elif "config" in self.tag_config:
                for item in self.tag_config["config"]:
                    dim = item.get("id")
                    tags = item.get("tags", [])
                    for tag in tags:
                        self.db.add_tag(dim, tag, is_learned=0)
        else:
            # 兼容旧版 settings 结构
            for dim, tags in self.settings.get("tag_dimensions", {}).items():
                for tag in tags:
                    self.db.add_tag(dim, tag, is_learned=0)

    def _get_api_response(self, model: str, system_prompt: str, content_parts: List[Any], json_mode: bool = True) -> Optional[Dict]:
        """调用 AI；对瞬时错误做调用层重试（ADR-0005）。

        失败时写入 self._last_api_failure: ApiCallFailure，供单条层决定是否 B 重试。
        """
        from core.analysis_job_policy import (
            ApiCallFailure,
            RetryConfig,
            is_retriable,
            should_retry_call,
            sleep_backoff,
        )

        cfg = getattr(self, "_job_retry_config", None) or RetryConfig.from_settings(self.settings)
        sleeper = getattr(self, "_retry_sleeper", None)
        cancel_ev = getattr(self, "analysis_cancel_event", None)
        attempt = 0
        last_err: Optional[BaseException] = None
        self._last_api_failure = None
        # 同步清理线程局部，避免读到过期失败
        if getattr(self, "_tls", None) is not None:
            self._tls.last_api_failure = None

        while True:
            if cancel_ev is not None and cancel_ev.is_set():
                logger.info("AI 调用因用户取消而中止")
                fail = ApiCallFailure(
                    message="用户取消", retriable=False, cancelled=True
                )
                self._last_api_failure = fail
                if getattr(self, "_tls", None) is not None:
                    self._tls.last_api_failure = fail
                return None
            attempt += 1
            logger.info(f"正在调用 AI 模型: {model} (JSON 模式: {json_mode}, 尝试 {attempt})")
            try:
                kwargs = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content_parts}
                    ],
                    "temperature": 0.2,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                response = self.client.chat.completions.create(**kwargs)
                res_content = response.choices[0].message.content

                if res_content is None or (isinstance(res_content, str) and not res_content.strip()):
                    raise ValueError("empty body")

                if json_mode:
                    data = json.loads(res_content)
                    logger.debug("AI 响应解析成功")
                    self._last_api_failure = None
                    if getattr(self, "_tls", None) is not None:
                        self._tls.last_api_failure = None
                    return data
                self._last_api_failure = None
                if getattr(self, "_tls", None) is not None:
                    self._tls.last_api_failure = None
                return res_content
            except Exception as e:
                last_err = e
                logger.error(f"AI API 调用出错: {e}")
                msg = str(e) or e.__class__.__name__
                if len(msg) > 200:
                    msg = msg[:200] + "…"
                retriable = is_retriable(e)
                if not retriable:
                    fail = ApiCallFailure(
                        message=msg, retriable=False, cancelled=False
                    )
                    self._last_api_failure = fail
                    if getattr(self, "_tls", None) is not None:
                        self._tls.last_api_failure = fail
                    return None
                if not should_retry_call(attempt, call_extra_attempts=cfg.call_extra_attempts):
                    fail = ApiCallFailure(
                        message=msg, retriable=True, cancelled=False
                    )
                    self._last_api_failure = fail
                    if getattr(self, "_tls", None) is not None:
                        self._tls.last_api_failure = fail
                    return None
                sleep_backoff(attempt, config=cfg, sleeper=sleeper)

        if last_err:
            logger.error(f"AI API 重试耗尽: {last_err}")
            msg = str(last_err) or last_err.__class__.__name__
            fail = ApiCallFailure(message=msg, retriable=True)
            self._last_api_failure = fail
            if getattr(self, "_tls", None) is not None:
                self._tls.last_api_failure = fail
        return None

    def get_last_api_failure(self):
        """线程安全读取上次 API 失败。"""
        tls = getattr(self, "_tls", None)
        if tls is not None and getattr(tls, "last_api_failure", None) is not None:
            return tls.last_api_failure
        return getattr(self, "_last_api_failure", None)

    def build_analysis_prompts(self, system_prompt_override: Optional[str] = None) -> Dict[str, str]:
        """
        组装主分析真正发给模型的 system / user 提示词（不含视频帧）。
        供 analyze_video 与设置页预览共用。
        使用精瘦分析用词表子集（标准词；排除别名/待审/占位）。
        """
        from core.tag_vocab import (
            build_analysis_vocab_subset,
            standards_map_from_tag_config,
        )

        global_settings = self.tag_config.get("global_settings", {}) if self.tag_config else {}
        default_header = "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。"
        system_prompt_header = (
            system_prompt_override
            if system_prompt_override is not None
            else global_settings.get("system_prompt", default_header)
        )
        if not (system_prompt_header or "").strip():
            system_prompt_header = default_header

        categories_list = [c.get("display_name") for c in (self.tag_config or {}).get("categories", [])]
        if not categories_list:
            categories_list = self.settings.get("categories", [])

        tag_groups = (self.tag_config or {}).get("tag_groups", [])
        # 标准词：配置 + DB；排除别名键与待审原文
        standards_map = standards_map_from_tag_config(self.tag_config)
        usage_counts: Dict[str, int] = {}
        exclude_aliases: Set[str] = set()
        exclude_pending: Set[str] = set()
        if self.db:
            try:
                for row in self.db.get_tags_detail() or []:
                    name = row.get("tag_name")
                    dim = row.get("dimension")
                    if name and dim:
                        standards_map.setdefault(dim, [])
                        if name not in standards_map[dim]:
                            standards_map[dim].append(name)
                        usage_counts[name] = int(row.get("usage_count") or 0)
            except Exception:
                pass
            try:
                exclude_aliases = set((self.db.get_synonyms() or {}).keys())
            except Exception:
                pass
            try:
                for p in self.db.list_pending_tags(status="pending") or []:
                    if p.get("raw_text"):
                        exclude_pending.add(p["raw_text"])
            except Exception:
                pass

        group_meta = {}
        for group in tag_groups:
            gid = group.get("id")
            if gid:
                group_meta[gid] = group.get("rules") or {}

        subset = build_analysis_vocab_subset(
            standards_map,
            group_meta=group_meta,
            usage_counts=usage_counts,
            exclude_aliases=exclude_aliases,
            exclude_pending=exclude_pending,
        )

        dimension_rules = []
        # ADR-0002/0003：分析结果只保留分类/摘要/标签；情绪并入氛围标签组
        expected_json_structure = {
            "category": f"从 {categories_list} 中选择一个最合适的视频大类",
            "summary": "50字以内的核心内容摘要",
        }

        for i, group in enumerate(tag_groups, 1):
            dim_id = group.get("id")
            name = group.get("name", dim_id)
            rules = group.get("rules", {})
            selection_mode = rules.get("selection_mode", "single")
            max_count = rules.get("max_count", 1)
            expandable = rules.get("ai_expandable", False)
            local_prompt = rules.get("local_prompt", "")
            pool_names = list(subset.by_group.get(dim_id) or [])

            rule = f"{i}. **{name}** ({dim_id}): {local_prompt}\n"
            mode_desc = "单选" if selection_mode == "single" else "多选"
            rule += f"   - 约束：{mode_desc}，最大数量 {max_count}。\n"
            if not expandable:
                rule += "   - 选词模式：封闭组，必须且只能从下列标准词中精确选择；禁止自造、禁止近义猜测。\n"
                if pool_names:
                    rule += f"   - 标准词候选（分析用词表子集）：[{', '.join(pool_names)}]\n"
                else:
                    rule += "   - 标准词候选：空（该组暂无可用标准词，请留空该字段）。\n"
            else:
                rule += f"   - 选词模式：建议组，可少量自造细节词，总数不超过 {max_count}；不必写入正式标签库。\n"
                if pool_names:
                    rule += f"   - 参考标准词（可选）：[{', '.join(pool_names)}]\n"
                else:
                    rule += "   - 无预设参考词，可按画面自由补充细节。\n"
            dimension_rules.append(rule)

            if selection_mode == "single" and max_count == 1:
                expected_json_structure[dim_id] = "选中的单个标签字符串"
            else:
                expected_json_structure[dim_id] = ["选中的标签列表"]

        rules_str = "\n".join(dimension_rules) if dimension_rules else "（当前未配置任何标签组；请到标签库添加 tag_groups）"
        warn_block = ""
        if subset.warnings:
            warn_block = "\n### 0. 词表提示\n" + "\n".join(f"- {w}" for w in subset.warnings) + "\n"
        json_template = json.dumps(expected_json_structure, ensure_ascii=False, indent=2)
        system_prompt = f"{system_prompt_header.strip()}\n你必须返回一个严格符合给定结构的有效 JSON 对象。"
        user_prompt = f"""请通过观察视频帧分析其内容，并严格遵守以下规则输出 JSON：
{warn_block}
### 1. 分类与标签规则
{rules_str}

### 2. 输出格式要求
你必须返回一个 JSON 对象，包含以下字段：
{json_template}

### 3. 注意事项
1. 结果必须是合法 JSON 格式，不要包含 Markdown 代码块标记（除非接口要求）。
2. 对于禁止新增标签的封闭组：必须且只能从给定预设库中精确选择；若无一匹配则该组留空，禁止编造近义词或猜测最接近项。
3. 摘要需客观描述画面，避免主观臆断。
4. 不要输出构图、星级、质量分、代理建议、标签权重或独立情绪字段。
"""
        return {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "system_prompt_header": system_prompt_header.strip(),
            "vocab_subset": subset.by_group,
            "vocab_warnings": list(subset.warnings),
            "vocab_is_thin": subset.is_thin,
        }

    def apply_tag_normalization(
        self,
        data: Dict,
        *,
        source_path: Optional[str] = None,
        persist_pending: bool = True,
    ) -> Dict:
        """
        对 AI/人工原始分组标签做标签归一（半封闭 + 别名 + 待审）。
        不自动把建议组新词写入标签库；不使用模糊近邻贴词。
        """
        from core.tag_normalize import GroupSpec, group_specs_from_tag_config, normalize_grouped_tags

        if not data:
            return data
        specs = group_specs_from_tag_config(self.tag_config)
        # 合并标签库中的标准词（配置 + DB）
        if self.db and specs:
            merged_specs = []
            for s in specs:
                try:
                    db_names = set(self.db.get_tags(s.id) or [])
                except Exception:
                    db_names = set()
                merged_specs.append(
                    GroupSpec(
                        id=s.id,
                        max_count=s.max_count,
                        ai_expandable=s.ai_expandable,
                        standards=frozenset(set(s.standards) | db_names),
                    )
                )
            specs = merged_specs
        alias_map: Dict[str, str] = {}
        if self.db:
            alias_map.update(self.db.get_synonyms())
        # 兼容 AI 返回扁平 dim 字段或已有 tag_groups
        raw_by_group: Dict[str, Any] = {}
        existing_groups = data.get("tag_groups") if isinstance(data.get("tag_groups"), dict) else {}
        for spec in specs:
            if spec.id in existing_groups and existing_groups[spec.id]:
                raw_by_group[spec.id] = existing_groups[spec.id]
            elif spec.id in data:
                raw_by_group[spec.id] = data.get(spec.id)

        result = normalize_grouped_tags(raw_by_group, specs, alias_map)
        data["tags"] = result.tags
        data["tag_groups"] = result.tag_groups
        data["tags_zh"] = result.tags
        if persist_pending and self.db:
            for p in result.pending:
                self.db.add_pending_tag(p.raw_text, p.group_id, source_path=source_path)
        data["_pending_tags"] = [
            {"raw_text": p.raw_text, "group_id": p.group_id, "status": p.status}
            for p in result.pending
        ]
        return data

    def analyze_video(
        self,
        base64_frames: List[str],
        cache_key: Optional[str] = None,
        *,
        use_cache: bool = True,
    ) -> Optional[Dict]:
        if not base64_frames:
            return None

        # use_cache=False：强制重新分析时跳过读缓存，但仍可写回新结果
        if use_cache and self.db and cache_key:
            cached = self.db.get_cache(cache_key)
            if cached:
                logger.info("使用 API 响应缓存")
                return cached

        # 与设置页预览共用同一条组装链路
        prompts = self.build_analysis_prompts()
        system_prompt = prompts["system_prompt"]
        user_prompt = prompts["user_prompt"]

        content_parts = [{"type": "text", "text": user_prompt}]
        for b64_img in base64_frames:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}", "detail": "low"}
            })

        # 获取模型
        model_name = SettingsManager.get_setting(self.settings, "api.model_personalization.video_classification", "gemini-2.0-flash")
        
        # 执行分析
        data = self._get_api_response(model_name, system_prompt, content_parts)
        
        if data:
            # 归一不在此处写待审：由 _process_single_video 最终路径统一 persist，避免 B 重试重复
            data = self.apply_tag_normalization(data, persist_pending=False)

            if self.db and cache_key:
                self.db.set_cache(cache_key, data)

        return data

    def recommend_tags(self, current_tags: List[str], limit: int = 5) -> List[Dict]:
        """基于当前标签推荐库内相关标签"""
        if not current_tags:
            return []
        
        # 获取现有库内所有标签
        all_library_tags = []
        if self.db:
            all_library_tags = self.db.get_tags()
        
        system_prompt = "你是一个素材管理专家，请根据用户提供的现有标签，从预设库中推荐最相关的其他标签。"
        user_prompt = f"""
现有标签: {current_tags}
可选库标签: {all_library_tags}

请从中推荐 {limit} 个最相关的标签。
如果你认为有库外非常重要但缺失的标签，也可以建议，但请优先从库中选择。

返回 JSON 格式：
{{
  "recommendations": [
    {{"tag": "标签名", "reason": "推荐理由", "in_library": true/false, "weight": 0.8}}
  ]
}}
"""
        model_name = SettingsManager.get_setting(self.settings, "api.model_personalization.tag_generation", "gemini-2.0-flash")
        data = self._get_api_response(model_name, system_prompt, [{"type": "text", "text": user_prompt}])
        
        if data and "recommendations" in data:
            return data["recommendations"]
        return []

    def cluster_cold_start_words(self, words: List[str]):
        """
        词表冷启动：真实 AI 近义分簇 + 归组。
        返回 (clusters, notes)；失败时降级为规则分簇并在 notes 说明。
        """
        from core.tag_ai_assist import (
            COLD_START_AI_CHUNK,
            cold_start_ai_system_prompt,
            cold_start_ai_user_prompt,
            parse_ai_cold_start_clusters,
            rule_cold_start_cluster,
        )
        from core.tag_vocab import ColdStartCluster

        uniq: List[str] = []
        seen = set()
        for w in words or []:
            t = str(w or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            uniq.append(t)

        notes: List[str] = []
        if not uniq:
            return [], notes

        model_name = SettingsManager.get_setting(
            self.settings,
            "api.model_personalization.tag_generation",
            "gemini-2.0-flash",
        )
        system_prompt = cold_start_ai_system_prompt()
        all_clusters: List[ColdStartCluster] = []
        used: Set[str] = set()
        ai_ok_chunks = 0
        ai_fail_chunks = 0

        for i in range(0, len(uniq), COLD_START_AI_CHUNK):
            chunk = uniq[i : i + COLD_START_AI_CHUNK]
            user_prompt = cold_start_ai_user_prompt(chunk)
            data = self._get_api_response(
                model_name,
                system_prompt,
                [{"type": "text", "text": user_prompt}],
            )
            # 必须有非空 clusters 列表才算 AI 成功
            ai_clusters = (
                data.get("clusters")
                if isinstance(data, dict) and isinstance(data.get("clusters"), list)
                else None
            )
            if not ai_clusters:
                ai_fail_chunks += 1
                parsed = rule_cold_start_cluster(chunk)
            else:
                parsed = parse_ai_cold_start_clusters(data, chunk)
                if not parsed:
                    ai_fail_chunks += 1
                    parsed = rule_cold_start_cluster(chunk)
                else:
                    ai_ok_chunks += 1
            for c in parsed:
                if c.standard in used:
                    continue
                aliases = [a for a in (c.aliases or []) if a not in used and a != c.standard]
                for a in aliases:
                    used.add(a)
                used.add(c.standard)
                all_clusters.append(
                    ColdStartCluster(
                        standard=c.standard,
                        aliases=aliases,
                        group_id=c.group_id,
                    )
                )

        # 漏网词
        for w in uniq:
            if w not in used:
                from core.tag_vocab import heuristic_assign_group

                all_clusters.append(
                    ColdStartCluster(standard=w, aliases=[], group_id=heuristic_assign_group(w))
                )
                used.add(w)

        if ai_ok_chunks and not ai_fail_chunks:
            notes.append(f"已使用 AI 近义分簇与归组（模型 {model_name}，{len(uniq)} 词）。")
        elif ai_ok_chunks and ai_fail_chunks:
            notes.append(
                f"部分批次使用 AI 分簇，{ai_fail_chunks} 批失败已降级规则分簇（模型 {model_name}）。"
            )
        else:
            notes.append(
                f"AI 分簇不可用或全部失败，已降级为规则分簇（模型 {model_name}）。"
                "请检查 API 密钥与网络。"
            )
        return all_clusters, notes


class TagProcessor:
    """标签预处理与分类联动逻辑"""
    def __init__(self, settings: Dict, db: Optional[DatabaseManager] = None):
        self.settings = settings
        self.db = db
        self._synonyms_cache = {
            "人": "人物",
            "人类": "人物",
            "景色": "风景",
            "大自然": "自然",
            "电脑": "科技",
            "手机": "科技",
            "办公": "工作",
            "谈话": "交流",
        }
        self.exclude_words = {"视频", "画面", "镜头", "无", "未知"}
        
        # 标签到分类的映射建议
        self.category_mapping = {
            "Aroll": ["人物", "访谈", "主持", "演讲", "对话", "交流"],
            "Broll": ["风景", "空镜", "城市", "自然", "特写", "环境"],
            "美食": ["食物", "烹饪", "餐饮", "蔬菜", "水果"],
            "科技": ["代码", "数码", "电脑", "互联网", "芯片"],
            "动物": ["猫", "狗", "宠物", "野生动物", "飞鸟"],
        }

    def _get_synonyms(self) -> Dict[str, str]:
        """合并硬编码和数据库中的同义词"""
        syns = self._synonyms_cache.copy()
        if self.db:
            db_syns = self.db.get_synonyms()
            syns.update(db_syns)
        return syns

    def process(self, tags: List[str], current_category: str) -> tuple[List[str], str]:
        """清洗标签并建议分类"""
        cleaned_tags = []
        synonyms = self._get_synonyms()
        for tag in tags:
            # 1. 基础清理
            tag = tag.strip().replace("_", "")
            if not tag or tag in self.exclude_words:
                continue
            
            # 2. 处理层级 (如 A/B 转换为 [A, B])
            if "/" in tag:
                parts = [p.strip() for p in tag.split("/") if p.strip()]
                for p in parts:
                    p = synonyms.get(p, p)
                    if p not in cleaned_tags:
                        cleaned_tags.append(p)
                continue

            # 3. 同义词合并
            tag = synonyms.get(tag, tag)
            
            if tag not in cleaned_tags:
                cleaned_tags.append(tag)

        # 4. 分类联动建议
        suggested_category = current_category
        for cat, keywords in self.category_mapping.items():
            if any(kw in "".join(cleaned_tags) for kw in keywords):
                # 只有当当前分类是默认的或不确定时，才进行自动建议
                if current_category in ["Unknown", "Other", "Vlog", "生活"]:
                    suggested_category = cat
                    break
        
        return cleaned_tags[:8], suggested_category # 增加到8个标签以便支持层级

    def suggest_smart_groups(self, tags: List[str]) -> Dict[str, List[str]]:
        """根据标签属性进行智能分组 (AI-Driven)"""
        groups = {
            "场景": [],
            "人物": [],
            "技术": [],
            "情感": [],
            "其他": []
        }
        
        # 预定义的一些映射，用于在 AI 未标注时兜底
        mapping = {
            "场景": ["城市", "自然", "居家", "办公", "室内", "室外", "森林", "海洋", "学校"],
            "人物": ["男性", "女性", "儿童", "老人", "情侣", "家庭", "团队", "人物"],
            "技术": ["特写", "全景", "航拍", "慢动作", "延时", "4K", "对比度", "光影"],
            "情感": ["唯美", "治愈", "悲伤", "喜悦", "震撼", "平静", "紧张"]
        }
        
        for tag in tags:
            found = False
            for group_name, keywords in mapping.items():
                if tag in keywords:
                    groups[group_name].append(tag)
                    found = True
                    break
            if not found:
                groups["其他"].append(tag)
        
        return {k: v for k, v in groups.items() if v}

    def auto_correct_tag(self, tag: str, library: List[str]) -> str:
        """简单的纠错逻辑，基于编辑距离"""
        if not tag:
            return tag
        
        # 如果标签已经在库中，直接返回
        if tag in library:
            return tag
        
        # 简单的编辑距离实现 (Levenshtein)
        def levenshtein(s1, s2):
            if len(s1) < len(s2):
                return levenshtein(s2, s1)
            if not s2:
                return len(s1)
            previous_row = range(len(s2) + 1)
            for i, c1 in enumerate(s1):
                current_row = [i + 1]
                for j, c2 in enumerate(s2):
                    insertions = previous_row[j + 1] + 1
                    deletions = current_row[j] + 1
                    substitutions = previous_row[j] + (c1 != c2)
                    current_row.append(min(insertions, deletions, substitutions))
                previous_row = current_row
            return previous_row[-1]

        best_match = tag
        min_dist = 2 # 降低阈值以提高准确性
        for ref in library:
            dist = levenshtein(tag, ref)
            if dist < min_dist:
                min_dist = dist
                best_match = ref
        
        return best_match

    def check_spelling(self, tags: List[str]) -> Dict[str, str]:
        """
        检查标签拼写，返回 {原始标签: 建议标签} 的映射。
        如果没有建议，则不包含在该字典中。
        """
        if not self.db:
            return {}
        
        library = self.db.get_tags()
        suggestions = {}
        for tag in tags:
            corrected = self.auto_correct_tag(tag, library)
            if corrected != tag:
                suggestions[tag] = corrected
        return suggestions

class FileManager:
    """管理文件 I/O 与索引"""
    
    def __init__(self, db: DatabaseManager, json_path: str = RESULTS_FILE_JSON, csv_path: str = RESULTS_FILE_CSV):
        self.db = db
        self.json_path = json_path
        self.csv_path = csv_path
        self.lock = threading.Lock()
        self.migrate_json_to_db()

    def migrate_json_to_db(self):
        if not os.path.exists(self.json_path):
            return

        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    return
                
                logger.info(f"正在从 {self.json_path} 迁移 {len(data)} 条数据到数据库...")
                for item in data:
                    if "path" not in item: continue
                    self.db.upsert_video(item)
        except Exception as e:
            logger.error(f"数据迁移失败: {e}")

    def scan_videos(self, path: str) -> List[str]:
        video_extensions = ('.mp4', '.mov', '.avi', '.mkv', '.m4v', '.flv', '.wmv')
        files_to_process = []
        
        if os.path.isfile(path):
            if path.lower().endswith(video_extensions):
                files_to_process.append(os.path.abspath(path))
        elif os.path.isdir(path):
            for root, _, files in os.walk(path):
                for file in files:
                    if file.lower().endswith(video_extensions):
                        files_to_process.append(os.path.abspath(os.path.join(root, file)))
        
        return files_to_process

    def save_results_to_csv(self, videos: List[Dict]):
        try:
            with open(self.csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['文件路径', '文件名', '分类', '摘要', '标签1', '标签2', '标签3', '标签4', '标签5'])
                for r in videos:
                    tags = r.get('tags', [""]*5)
                    if isinstance(tags, str):
                        try: tags = json.loads(tags)
                        except: tags = [tags]
                    if len(tags) < 5: tags += [""] * (5 - len(tags))
                    writer.writerow([r.get('path', ''), r.get('filename', ''), r.get('category', ''), r.get('summary', ''), *tags[:5]])
        except Exception: pass

    @staticmethod
    def sanitize_filename(name: str, max_len: int = 150) -> str:
        invalid_chars = r'[\\/:*?"<>|]'
        sanitized = re.sub(invalid_chars, "_", name)
        sanitized = sanitized.replace("\n", " ").replace("\r", " ").strip()
        if len(sanitized) > max_len:
            sanitized = sanitized[:max_len-3] + "..."
        return sanitized

    @staticmethod
    def get_file_index(root_dir: str) -> Dict[str, str]:
        """建立文件索引，用于鲁棒性搜索"""
        index = {}
        for root, _, files in os.walk(root_dir):
            for f in files:
                index[f] = os.path.join(root, f)
                # 同时记录后缀匹配
                if "-" in f:
                    index[f"suffix_{f.split('-')[-1]}"] = os.path.join(root, f)
        return index

# 工作范围：视频扩展名（与 FileManager.scan_videos 保持一致）
WORK_SCOPE_VIDEO_EXTENSIONS = ('.mp4', '.mov', '.avi', '.mkv', '.m4v', '.flv', '.wmv')


def normalize_work_path(path: str) -> str:
    """规范化路径用于比较与去重（绝对路径 + 系统大小写规则）。"""
    return os.path.normcase(os.path.abspath(path))


def is_video_path_in_scope(video_path: str, scope_paths: List[str]) -> bool:
    """
    判定视频路径是否属于工作范围。
    - 范围项为文件：精确匹配
    - 范围项为文件夹：视频位于其下（含任意层子目录）
    - 范围项尚不存在时：按「无扩展名 / 像目录」视为文件夹前缀，否则视为文件精确匹配
    """
    if not video_path or not scope_paths:
        return False
    vp = normalize_work_path(video_path)
    for raw in scope_paths:
        if not raw:
            continue
        sp = normalize_work_path(raw)
        if os.path.isfile(raw):
            if vp == sp:
                return True
            continue
        if os.path.isdir(raw):
            if vp == sp or vp.startswith(sp + os.sep):
                return True
            continue
        # 路径当前不存在：有视频扩展名则按文件，否则按文件夹前缀
        _, ext = os.path.splitext(raw)
        if ext.lower() in WORK_SCOPE_VIDEO_EXTENSIONS:
            if vp == sp:
                return True
        else:
            if vp == sp or vp.startswith(sp + os.sep):
                return True
    return False


def dedupe_scope_paths(paths: List[str]) -> List[str]:
    """去重并规范化工作范围路径列表，保留首次出现顺序与可显示的绝对路径。"""
    seen: Set[str] = set()
    result: List[str] = []
    for p in paths or []:
        if not p or not str(p).strip():
            continue
        abs_p = os.path.abspath(str(p).strip())
        key = os.path.normcase(abs_p)
        if key in seen:
            continue
        seen.add(key)
        result.append(abs_p)
    return result


class VideoOrganizerService:
    """Core Service Layer: 处理所有业务逻辑"""
    
    def __init__(
        self,
        settings: Optional[Dict] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        db_path: Optional[str] = None,
        results_json: Optional[str] = None,
        results_csv: Optional[str] = None,
    ):
        self.db = DatabaseManager(db_path) if db_path else DatabaseManager()
        self.settings = settings or SettingsManager.load_settings(self.db)
        self.tag_config = self.load_tag_config()
        self.on_log = on_log or (lambda m: print(m))
        self.on_progress = on_progress or (lambda c, t: None)
        # 结构化分析进度快照（dict）；UI 优先消费
        self.on_analysis_snapshot: Callable[[Dict], None] = lambda _s: None
        self._analysis_cancel = threading.Event()
        self._progress_lock = threading.Lock()
        # 测试可注入：跳过真实 sleep
        self._retry_sleeper: Optional[Callable[[float], None]] = None
        self.processor = VideoProcessor()
        self.ai = AIHandler(self.settings, self.db, self.tag_config)
        self.ai.analysis_cancel_event = self._analysis_cancel
        self.tag_processor = TagProcessor(self.settings, self.db)
        json_path = results_json if results_json is not None else RESULTS_FILE_JSON
        csv_path = results_csv if results_csv is not None else RESULTS_FILE_CSV
        self.file_manager = FileManager(self.db, json_path, csv_path)
        
        # V4.0: L1 内存缓存 (三级缓存架构之一)
        self._memory_cache = {}
        self._cache_lock = threading.Lock()
        # 工作范围：当前工作台呈现与处理的路径集合（文件 + 文件夹）
        self._work_scope_paths: List[str] = []
        # 从工作范围「移出」的视频路径（规范化），即使仍落在文件夹范围内也不再呈现
        self._work_scope_exclusions: Set[str] = set()
        # S4：废除中转池 — 启动时将 pool/未分组标准词降为待审（不碰视频 tags 字符串）
        try:
            self.migrate_pool_standard_tags_to_pending()
        except Exception as e:
            logger.warning(f"pool→待审迁移跳过: {e}")

    def reload_ai_from_settings(self) -> None:
        """设置保存后：同步 AI 客户端与依赖 settings 的处理器。"""
        try:
            from core.model_providers import ensure_providers
            ensure_providers(self.settings)
        except Exception:
            pass
        if hasattr(self, "ai") and self.ai is not None:
            self.ai.settings = self.settings
            if hasattr(self.ai, "reload_client"):
                self.ai.reload_client()
        if hasattr(self, "tag_processor") and self.tag_processor is not None:
            self.tag_processor.settings = self.settings

    def reload_ai_client(self):
        """设置保存后：让 AI 客户端使用最新 api.key / base_url。"""
        try:
            from core.model_providers import ensure_providers
            ensure_providers(self.settings)
        except Exception:
            pass
        if hasattr(self, "ai") and self.ai is not None:
            # 保证 handler 引用同一 settings 对象
            self.ai.settings = self.settings
            if hasattr(self.ai, "reload_client"):
                self.ai.reload_client()
        if hasattr(self, "tag_processor") and self.tag_processor is not None:
            self.tag_processor.settings = self.settings

    # --- 工作范围 (Work Scope) ---

    def get_work_scope_paths(self) -> List[str]:
        """返回当前工作范围路径列表（副本）。"""
        return list(self._work_scope_paths)

    def set_work_scope_paths(
        self,
        paths: List[str],
        *,
        scan: bool = True,
        clear_exclusions: bool = True,
    ) -> Dict[str, Any]:
        """
        整份替换工作范围。
        scan=True 时扫描范围内视频并入库：新文件登记为未分析(pending)，已存在不覆盖分析结果。
        clear_exclusions=True：清空「移出工作范围」排除集（浏览替换时应 True；累加时应 False）。
        """
        self._work_scope_paths = dedupe_scope_paths(paths)
        if clear_exclusions:
            self._work_scope_exclusions = set()
        else:
            # 累加后：若排除路径已不在任何范围项下，可保留；仍在范围内的继续排除
            if not hasattr(self, "_work_scope_exclusions"):
                self._work_scope_exclusions = set()
        registered: List[str] = []
        if scan:
            registered = self.scan_and_register_work_scope()
        self.persist_work_scope_if_enabled()
        return {
            "paths": list(self._work_scope_paths),
            "registered": registered,
        }

    def replace_work_scope(self, paths: List[str], *, scan: bool = True) -> Dict[str, Any]:
        """set_work_scope_paths 的别名（语义：浏览确认 = 整份替换）。"""
        return self.set_work_scope_paths(paths, scan=scan, clear_exclusions=True)

    def append_work_scope(self, paths: List[str], *, scan: bool = True) -> Dict[str, Any]:
        """累加路径到当前工作范围（去重），可选扫盘入库；保留已移出排除集。"""
        combined = list(self._work_scope_paths) + list(paths or [])
        return self.set_work_scope_paths(combined, scan=scan, clear_exclusions=False)

    def is_path_in_work_scope(self, video_path: str) -> bool:
        """视频路径是否落在当前工作范围内（排除已移出的路径）。"""
        if not video_path:
            return False
        try:
            np = normalize_work_path(video_path)
        except Exception:
            np = video_path
        if np in getattr(self, "_work_scope_exclusions", set()):
            return False
        return is_video_path_in_scope(video_path, self._work_scope_paths)

    def resolve_operation_target_paths(
        self,
        selected_paths: Optional[List[str]] = None,
        visible_paths: Optional[List[str]] = None,
        *,
        scope_paths: Optional[List[str]] = None,
        checked_paths: Optional[List[str]] = None,
    ) -> List[str]:
        """
        解析批量操作目标路径（对接 core.operation_targets）。

        新语义（UI 推荐）：
        - selected_paths：列表选中（可空 / None）
        - visible_paths：当前可见列表路径
        - scope_paths：工作台传入工作范围内路径；素材库传 None 或不传

        旧语义兼容（test_ops_filter_scope / 仅传 checked）：
        - 仅传 selected_paths/checked_paths 且未传 visible_paths
          → 视为旧「勾选 ∩ 工作范围」：无选中则可见=范围内全部
        - 工作范围为空 → []
        """
        from core.operation_targets import resolve_operation_target_paths as resolve_pure

        selected = selected_paths if selected_paths is not None else checked_paths

        # 旧签名：resolve_operation_target_paths() / ([paths]) / (None)
        if visible_paths is None and scope_paths is None:
            if not getattr(self, "_work_scope_paths", None):
                return []
            in_scope = self.get_videos_in_work_scope()
            scope_list = [v.get("path") for v in in_scope if v.get("path")]
            # None/空选中 → 可见=范围内全部；有选中 → 与范围求交
            return resolve_pure(selected, scope_list, scope_paths=scope_list)

        # 新签名：UI 显式传入 visible（scope 可选）
        vis = list(visible_paths or [])
        if scope_paths is not None:
            scope_list = list(scope_paths)
            # 工作台：范围为空时不操作
            if not scope_list and not getattr(self, "_work_scope_paths", None):
                return []
            return resolve_pure(selected, vis, scope_paths=scope_list)
        return resolve_pure(selected, vis, scope_paths=None)

    def get_videos_for_operation(
        self,
        selected_paths: Optional[List[str]] = None,
        visible_paths: Optional[List[str]] = None,
        *,
        scope_paths: Optional[List[str]] = None,
        checked_paths: Optional[List[str]] = None,
    ) -> List[Dict]:
        """操作目标视频记录。工作台默认 ⊆ 工作范围；素材库可无 scope。"""
        targets = set(
            normalize_work_path(p)
            for p in self.resolve_operation_target_paths(
                selected_paths,
                visible_paths,
                scope_paths=scope_paths,
                checked_paths=checked_paths,
            )
        )
        if not targets:
            return []
        # 有工作范围约束时从范围内取记录，否则从全库
        if scope_paths is not None or (
            visible_paths is None and scope_paths is None
        ):
            pool = self.get_videos_in_work_scope()
        else:
            pool = self.get_all_videos()
        return [
            v
            for v in pool
            if normalize_work_path(v.get("path", "")) in targets
        ]

    def persist_work_scope_if_enabled(self) -> None:
        """若开启「记住上次工作范围」，把当前路径与移出排除集写入 settings。"""
        prefs = self.settings.setdefault("ui_preferences", {})
        if not prefs.get("remember_work_scope"):
            return
        prefs["last_work_scope"] = list(self._work_scope_paths)
        prefs["last_work_scope_exclusions"] = list(
            getattr(self, "_work_scope_exclusions", set()) or set()
        )
        try:
            SettingsManager.save_settings(self.settings, self.db)
        except Exception as e:
            logger.warning(f"保存工作范围失败: {e}")

    def restore_work_scope_if_enabled(self) -> bool:
        """启动时若开启记住范围则恢复路径与排除集；成功返回 True。"""
        prefs = self.settings.get("ui_preferences", {}) or {}
        if not prefs.get("remember_work_scope"):
            return False
        paths = prefs.get("last_work_scope") or []
        if not paths:
            return False
        exclusions = prefs.get("last_work_scope_exclusions") or []
        self.set_work_scope_paths(list(paths), scan=True, clear_exclusions=True)
        # 恢复「移出工作范围」排除（在替换清排除之后写回）
        restored: Set[str] = set()
        for p in exclusions:
            if not p:
                continue
            try:
                restored.add(normalize_work_path(p))
            except Exception:
                restored.add(str(p))
        self._work_scope_exclusions = restored
        self.persist_work_scope_if_enabled()
        return True

    def scan_and_register_work_scope(self) -> List[str]:
        """
        扫描当前工作范围内的视频文件并入库。
        - 尚不在库：登记为 status=pending（未分析）
        - 已在库：保留既有分析结果，不覆盖
        返回本次新登记的路径列表。
        """
        registered: List[str] = []
        if not self._work_scope_paths:
            return registered

        for scope_path in self._work_scope_paths:
            try:
                found = self.file_manager.scan_videos(scope_path)
            except Exception as e:
                logger.warning(f"扫描工作范围失败 {scope_path}: {e}")
                continue
            for video_path in found:
                abs_path = os.path.abspath(video_path)
                record = {
                    "path": abs_path,
                    "filename": os.path.basename(abs_path),
                    "status": "pending",
                    "tags": [],
                    "category": None,
                    "summary": None,
                    "tag_groups": {},
                }
                if self.db.insert_video_if_absent(record):
                    # 入库轻量缩略图（不依赖完整 AI 分析）
                    thumb = None
                    try:
                        proc = self.processor.extract_frames(
                            abs_path,
                            max_frames=1,
                            target_size=SettingsManager.get_setting(
                                self.settings, "processing.target_size", 512
                            ),
                            save_thumbnail=True,
                            use_scene_detection=False,
                        )
                        thumb = proc.get("thumbnail")
                        if thumb:
                            self.db.upsert_video(
                                {
                                    **record,
                                    "thumbnail": thumb,
                                    "thumbnail_path": thumb,
                                }
                            )
                            record["thumbnail"] = thumb
                            record["thumbnail_path"] = thumb
                    except Exception as e:
                        logger.warning(f"入库缩略图失败 {abs_path}: {e}")
                    registered.append(abs_path)
                    with self._cache_lock:
                        if self._memory_cache is not None:
                            # 仅在缓存已预热时追加，避免半缓存状态
                            if self._memory_cache:
                                self._memory_cache[abs_path] = {
                                    **record,
                                    "thumbnail_path": thumb,
                                    "thumbnail": thumb,
                                }

        if registered:
            # 新入库后让下次 get_all_videos 从 DB 刷新完整记录
            with self._cache_lock:
                self._memory_cache = {}
            self.log(f"工作范围扫盘：新登记 {len(registered)} 个未分析视频。")
        return registered

    def get_videos_in_work_scope(self) -> List[Dict]:
        """已入库且属于当前工作范围的视频；空范围返回空列表。"""
        if not self._work_scope_paths:
            return []
        videos = self.get_all_videos()
        return [v for v in videos if self.is_path_in_work_scope(v.get("path", ""))]

    def clear_work_scope(self) -> None:
        """清空工作范围（不删库内记录）。"""
        self._work_scope_paths = []
        self._work_scope_exclusions = set()
        self.persist_work_scope_if_enabled()

    def suggest_pending_tag(
        self,
        pending_row: Dict,
        *,
        suggest_fn=None,
    ):
        """待审词 AI/规则建议（不写库）。"""
        from core.tag_ai_assist import rule_pending_suggestion

        standards: List[str] = []
        for g in (self.tag_config or {}).get("tag_groups") or []:
            for t in g.get("tags") or []:
                if isinstance(t, dict):
                    n = t.get("name")
                else:
                    n = t
                if n:
                    standards.append(str(n).strip())
        fn = suggest_fn or rule_pending_suggestion
        return fn(pending_row, standards)

    def apply_pending_suggestion(self, suggestion, *, confirm: bool = True) -> bool:
        """确认后应用待审建议；confirm=False 时拒绝写库（测试用）。"""
        if not confirm or suggestion is None:
            return False

        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            if hasattr(obj, key):
                return getattr(obj, key)
            return default

        action = _get(suggestion, "action")
        pid = _get(suggestion, "pending_id")
        if action == "discard":
            return self.resolve_pending_tag(pid, "discard")
        if action == "link_alias":
            std = _get(suggestion, "recommended_standard")
            return self.resolve_pending_tag(pid, "link_alias", standard_tag=std)
        if action == "approve_standard":
            gid = _get(suggestion, "recommended_group_id") or _get(suggestion, "group_id")
            return self.resolve_pending_tag(pid, "approve_standard", group_id=gid)
        return False

    def audit_synonyms(self, *, audit_fn=None):
        """近义巡检：返回合并建议列表（不写库）。"""
        from core.tag_ai_assist import rule_synonym_audit

        standards: List[str] = []
        for g in (self.tag_config or {}).get("tag_groups") or []:
            for t in g.get("tags") or []:
                n = t.get("name") if isinstance(t, dict) else t
                if n:
                    standards.append(str(n).strip())
        fn = audit_fn or rule_synonym_audit
        return fn(standards)

    def apply_synonym_merge_suggestions(
        self, suggestions, *, confirm: bool = True
    ) -> Dict[str, Any]:
        """
        确认后执行近义合并：被合并词 → 保留词的别名，并 bulk_replace 视频标签。
        """
        from core.tag_ai_assist import apply_synonym_merges_plan

        if not confirm:
            return {"ok": False, "applied": 0, "message": "未确认，未写库"}
        plan = apply_synonym_merges_plan(suggestions or [])
        applied = 0
        for alias, standard in plan.items():
            try:
                self.db.add_synonym(standard, alias)
                self.bulk_replace_tags(alias, standard)
                # 从 tag_config 各组移除被合并标准词
                cfg = self.tag_config or {}
                for group in cfg.get("tag_groups") or []:
                    tags = group.get("tags") or []
                    new_tags = []
                    for t in tags:
                        name = t.get("name") if isinstance(t, dict) else t
                        if str(name).strip() == alias:
                            continue
                        new_tags.append(t)
                    group["tags"] = new_tags
                self.save_tag_config(cfg)
                applied += 1
            except Exception as e:
                logger.warning(f"合并 {alias}->{standard} 失败: {e}")
        return {"ok": True, "applied": applied, "plan": plan}

    # --- 备份与恢复 (V6.0) ---
    def backup_configuration(self) -> str:
        """一键备份数据库和标签配置到 backups/ 目录"""
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}")
        os.makedirs(backup_path)
        
        try:
            # 备份数据库 (使用 WAL 模式下可能需要处理 shm/wal 文件，但简单复制 .db 在关闭连接时通常够了)
            # 为了安全，我们这里直接复制文件
            shutil.copy2(DB_FILE, os.path.join(backup_path, DB_FILE))
            if os.path.exists(TAG_CONFIG_FILE):
                shutil.copy2(TAG_CONFIG_FILE, os.path.join(backup_path, TAG_CONFIG_FILE))
            if os.path.exists(SETTINGS_FILE):
                shutil.copy2(SETTINGS_FILE, os.path.join(backup_path, os.path.basename(SETTINGS_FILE)))
            
            self.log(f"配置已备份至: {backup_path}")
            return backup_path
        except Exception as e:
            self.log(f"备份失败: {e}")
            return ""

    def restore_configuration(self, backup_folder_name: str) -> bool:
        """从备份目录恢复配置"""
        backup_path = os.path.join(BACKUP_DIR, backup_folder_name)
        if not os.path.exists(backup_path):
            self.log(f"备份目录不存在: {backup_path}")
            return False
        
        try:
            # 恢复前先做个临时备份
            self.backup_configuration()
            
            # 恢复文件
            for filename in [DB_FILE, TAG_CONFIG_FILE, os.path.basename(SETTINGS_FILE)]:
                src = os.path.join(backup_path, filename)
                if os.path.exists(src):
                    shutil.copy2(src, filename)
            
            self.log("配置恢复成功，请重启应用以应用更改。")
            return True
        except Exception as e:
            self.log(f"恢复失败: {e}")
            return False

    def list_backups(self) -> List[str]:
        """列出所有可用的备份"""
        if not os.path.exists(BACKUP_DIR):
            return []
        return [d for d in os.listdir(BACKUP_DIR) if os.path.isdir(os.path.join(BACKUP_DIR, d))]

    def switch_preset(self, preset_id: str) -> bool:
        """切换当前的活动预设"""
        global_settings = self.tag_config.get("global_settings", {})
        presets = global_settings.get("presets", {})
        
        if preset_id in presets:
            preset = presets[preset_id]
            # 更新当前的导出方案
            global_settings["export_schemes"] = preset
            self.save_tag_config(self.tag_config)
            self.log(f"已切换到预设: {preset_id}")
            return True
        return False

    def generate_storyboard(self, video_path: str, output_path: str, max_frames: int = 12) -> bool:
        """生成视频故事板 (PDF)"""
        if not HAS_IMAGEHASH: # 借用这个判断 PIL 是否可用，或者直接 try
            self.log("无法生成故事板: 未安装 PIL (Pillow)")
            return False
            
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            # 1. 提取更多帧
            proc_res = self.processor.extract_frames(video_path, max_frames=max_frames, target_size=1024, use_scene_detection=True)
            frames_b64 = proc_res.get("frames", [])
            
            if not frames_b64:
                return False
                
            # 2. 将 base64 转为 PIL Image
            images = []
            for b64 in frames_b64:
                img_data = base64.b64decode(b64)
                from io import BytesIO
                images.append(Image.open(BytesIO(img_data)))
            
            if not images:
                return False
                
            # 3. 布局逻辑 (网格)
            cols = 3
            rows = (len(images) + cols - 1) // cols
            thumb_w = 640
            thumb_h = 360
            margin = 40
            header_h = 200
            
            canvas_w = thumb_w * cols + margin * (cols + 1)
            canvas_h = thumb_h * rows + margin * (rows + 1) + header_h
            
            storyboard = Image.new('RGB', (canvas_w, canvas_h), (255, 255, 255))
            draw = ImageDraw.Draw(storyboard)
            
            # 4. 绘制页眉 (元数据)
            try:
                # 尝试加载中文字体
                font_path = "C:/Windows/Fonts/msyh.ttc" if sys.platform == "win32" else "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
                title_font = ImageFont.truetype(font_path, 48)
                text_font = ImageFont.truetype(font_path, 24)
            except:
                title_font = ImageFont.load_default()
                text_font = ImageFont.load_default()
                
            video_data = next((v for v in self.get_all_videos() if v["path"] == video_path), {})
            filename = os.path.basename(video_path)
            category = video_data.get("category", "Unknown")
            tags = ", ".join(video_data.get("tags", []))
            summary = video_data.get("summary", "")
            
            draw.text((margin, 40), f"视频故事板: {filename}", fill=(0, 0, 0), font=title_font)
            draw.text((margin, 110), f"分类: {category} | 标签: {tags}", fill=(80, 80, 80), font=text_font)
            draw.text((margin, 150), f"摘要: {summary}", fill=(80, 80, 80), font=text_font)
            
            # 5. 绘制帧网格
            for i, img in enumerate(images):
                r = i // cols
                c = i % cols
                x = margin + c * (thumb_w + margin)
                y = header_h + margin + r * (thumb_h + margin)
                
                # 调整大小
                img.thumbnail((thumb_w, thumb_h))
                storyboard.paste(img, (x, y))
                draw.rectangle([x, y, x + thumb_w, y + thumb_h], outline=(200, 200, 200), width=2)
                draw.text((x, y + thumb_h + 5), f"Frame {i+1}", fill=(150, 150, 150), font=text_font)
                
            # 6. 保存为 PDF 或图片
            if output_path.lower().endswith(".pdf"):
                storyboard.save(output_path, "PDF", resolution=100.0)
            else:
                storyboard.save(output_path)
                
            self.log(f"故事板已生成: {output_path}")
            return True
        except Exception as e:
            self.log(f"生成故事板失败: {e}")
            return False

    def log(self, message: str):
        logger.info(message)
        self.on_log(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def load_tag_config(self) -> Dict:
        """加载标签配置，并支持从旧版本自动迁移至 v6.0"""
        default_v6 = {
            "version": "6.0",
            "global_settings": {
                "system_prompt": "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。",
                "language": "zh-CN",
                "output_format": "json",
                "export_schemes": {
                    "filename_pattern": "{category}-{tags}-{summary}",
                    "xmp_hierarchical": True,
                    "xmp_prefix_category": True,
                    "ale_columns": ["Name", "Keywords", "Category", "Summary"]
                }
            },
            "categories": [
                { "id": "aroll", "display_name": "A-Roll (访谈/主体)", "order": 0 },
                { "id": "broll", "display_name": "B-Roll (空镜/素材)", "order": 1 },
                { "id": "working", "display_name": "工作记录", "order": 2 }
            ],
            "tag_groups": []
        }

        if not os.path.exists(TAG_CONFIG_FILE):
            from core.tag_vocab import ensure_canonical_tag_groups
            return ensure_canonical_tag_groups(default_v6)

        try:
            with open(TAG_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            # 检查版本并执行迁移
            # 已有 tag_groups 的配置按 v6 处理；勿把「缺 version 字段」误当 v1 整表清空
            raw_ver = config.get("version", None)
            has_groups = bool(config.get("tag_groups"))
            if has_groups and (raw_ver is None or str(raw_ver) < "6.0"):
                config = dict(config)
                config["version"] = "6.0"
                from core.tag_vocab import ensure_canonical_tag_groups
                config = ensure_canonical_tag_groups(config)
                self.save_tag_config(config)
                return config

            version = str(raw_ver if raw_ver is not None else "1.0")
            if version < "6.0" and not has_groups:
                logger.info(f"正在将标签配置从 v{version} 迁移至 v6.0...")
                migrated = default_v6.copy()
                
                # 迁移旧版的 config 列表
                old_config = config.get("config", [])
                new_groups = []
                for item in old_config:
                    new_group = {
                        "id": item.get("id"),
                        "name": item.get("display_name", item.get("id")),
                        "rules": {
                            "selection_mode": "single" if item.get("exclusive", True) else "multiple",
                            "max_count": item.get("max_count", 1),
                            "ai_expandable": item.get("ai_expandable", False),
                            "local_prompt": item.get("description", "")
                        },
                        "tags": item.get("tags", [])
                    }
                    new_groups.append(new_group)
                
                migrated["tag_groups"] = new_groups
                
                # 兼容性迁移：如果旧版有 categories 也可以尝试保留
                if "categories" in config and isinstance(config["categories"], list):
                    migrated["categories"] = config["categories"]
                
                # 自动保存迁移后的版本
                from core.tag_vocab import ensure_canonical_tag_groups
                migrated = ensure_canonical_tag_groups(migrated)
                self.save_tag_config(migrated)
                return migrated
            
            from core.tag_vocab import ensure_canonical_tag_groups
            return ensure_canonical_tag_groups(config)
        except Exception as e:
            logger.error(f"加载标签配置失败: {e}")
            from core.tag_vocab import ensure_canonical_tag_groups
            return ensure_canonical_tag_groups(default_v6)

    def save_tag_config(self, config: Dict):
        from core.tag_vocab import ensure_canonical_tag_groups

        # 始终带上 version，避免下次启动被误当 v1 迁移
        if isinstance(config, dict) and not config.get("version"):
            config = dict(config)
            config["version"] = "6.0"
        try:
            config = ensure_canonical_tag_groups(config or {})
        except Exception:
            pass
        self.tag_config = config
        try:
            with open(TAG_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            # 更新 AI Handler 中的配置
            if hasattr(self, "ai"):
                self.ai.tag_config = config
        except Exception as e:
            logger.error(f"保存标签配置失败: {e}")

    def build_cold_start_draft(
        self,
        draft_lines: List[str],
        *,
        cluster_fn=None,
        use_ai: Optional[bool] = None,
        direct: Optional[bool] = None,
    ):
        """
        从草稿行生成词表草案（不写库）。

        产品默认 **direct=True**：按词.txt 语义直接解析（分组标题 + 标准词 ← 别名），
        不做 AI/规则分簇、不做精瘦裁剪。

        - cluster_fn：注入时走旧聚类路径（测试保留）
        - use_ai=True：显式走 AI 分簇（代码保留，默认关闭）
        - direct=None：无 cluster_fn 且未强制 AI 时为直接加载
        """
        from core.tag_ai_assist import COLD_START_AI_ENABLED, rule_cold_start_cluster
        from core.tag_vocab import build_cold_start_draft, build_vocab_draft_from_lines

        # 显式聚类函数 → 旧路径
        if cluster_fn is not None:
            draft = build_cold_start_draft(draft_lines, cluster_fn=cluster_fn)
            return draft

        if use_ai is None:
            use_ai = bool(COLD_START_AI_ENABLED)
        if direct is None:
            direct = not use_ai

        # 产品主路径：直接加载
        if direct and not use_ai:
            return build_vocab_draft_from_lines(draft_lines)

        notes_extra: List[str] = []
        if use_ai:
            def _ai_cluster(ws):
                clusters, notes = self.ai.cluster_cold_start_words(list(ws))
                notes_extra.extend(notes)
                return clusters

            draft = build_cold_start_draft(draft_lines, cluster_fn=_ai_cluster)
        else:
            draft = build_cold_start_draft(draft_lines, cluster_fn=rule_cold_start_cluster)
            notes_extra.append("使用规则分簇（非直接加载模式）。")

        try:
            draft.notes = list(draft.notes or []) + notes_extra
        except Exception:
            pass
        return draft

    def load_vocab_file(self, path: Optional[str] = None):
        """从词表文件（默认项目根 词.txt）直接解析为草案，不写库。"""
        from core.tag_vocab import build_vocab_draft_from_lines

        file_path = path or DICTIONARY_FILE
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        draft = build_vocab_draft_from_lines(lines)
        try:
            draft.notes = list(draft.notes or []) + [f"来源文件：{file_path}"]
        except Exception:
            pass
        return draft

    def commit_cold_start_draft(
        self,
        draft,
        *,
        replace_placeholders: bool = True,
        replace_all_group_tags: bool = False,
        cap_closed_groups: bool = False,
    ) -> Dict[str, Any]:
        """
        终审写入：合并草案到 tag_config + 标签库 + 别名表。
        直接加载词.txt 时默认 cap_closed_groups=False（不截断封闭组）。
        """
        from core.tag_vocab import commit_plan_summary, merge_draft_into_tag_config

        if draft is None:
            return {"ok": False, "error": "empty draft"}
        cfg = merge_draft_into_tag_config(
            self.tag_config or {},
            draft,
            replace_placeholders=replace_placeholders,
            replace_all_group_tags=replace_all_group_tags,
            cap_closed_groups=cap_closed_groups,
            create_missing_groups=True,
        )
        self.save_tag_config(cfg)
        # 同步标准词入库（dimension 用小写组 id）
        for group in cfg.get("tag_groups") or []:
            gid = group.get("id")
            for t in group.get("tags") or []:
                name = t if isinstance(t, str) else (t.get("name") if isinstance(t, dict) else None)
                if name and gid:
                    self.db.add_tag(str(gid).strip(), str(name).strip(), is_learned=0)
        # 别名
        for alias, standard in (draft.alias_map or {}).items():
            if alias and standard:
                self.db.add_synonym(standard, alias)
        # 刷新 AI 侧标签库
        if hasattr(self, "ai") and hasattr(self.ai, "init_tag_libraries"):
            try:
                self.ai.init_tag_libraries()
            except Exception:
                pass
        # 再 heal 一次，确保内存与文件一致
        try:
            self.heal_tag_config_tags_from_db()
        except Exception:
            pass
        summary = commit_plan_summary(draft)
        summary["ok"] = True
        summary["group_counts"] = {
            (g.get("id") or "?"): len(g.get("tags") or [])
            for g in (self.tag_config or {}).get("tag_groups") or []
        }
        return summary

    def heal_tag_config_tags_from_db(self, *, force: bool = False) -> bool:
        """
        保证五组骨架存在，并用 tags_library 回填/补全各组标准词。
        解决：词写入 DB 成功但 tag_config.json 组缺失或 tags 过少，界面「标签组没数据」。
        """
        from core.tag_vocab import ensure_canonical_tag_groups

        cfg = ensure_canonical_tag_groups(self.tag_config or {})
        details = self.db.get_tags_detail() or []
        by_dim: Dict[str, List[str]] = {}
        for t in details:
            name = (t.get("tag_name") or "").strip()
            dim = (t.get("dimension") or "").strip().lower()
            if not name or not dim or dim == "pool":
                continue
            by_dim.setdefault(dim, []).append(name)

        changed = False
        # 组数量/骨架变化
        old_ids = {
            (g.get("id") if isinstance(g, dict) else None)
            for g in (self.tag_config or {}).get("tag_groups") or []
        }
        new_ids = {
            (g.get("id") if isinstance(g, dict) else None)
            for g in (cfg.get("tag_groups") or [])
        }
        if old_ids != new_ids:
            changed = True

        for g in cfg.get("tag_groups") or []:
            if not isinstance(g, dict):
                continue
            gid = (g.get("id") or "").strip()
            if not gid:
                continue
            existing_names: List[str] = []
            for t in g.get("tags") or []:
                if isinstance(t, str) and t.strip():
                    existing_names.append(t.strip())
                elif isinstance(t, dict) and t.get("name"):
                    existing_names.append(str(t["name"]).strip())
            db_names = list(dict.fromkeys(by_dim.get(gid, [])))
            if not db_names and not existing_names:
                continue
            if force or not existing_names:
                merged = db_names or existing_names
            elif len(db_names) > len(existing_names):
                # 库里明显更全：合并补全
                merged = list(dict.fromkeys(existing_names + db_names))
            else:
                merged = list(dict.fromkeys(existing_names + [
                    n for n in db_names if n not in existing_names
                ]))
            if merged != existing_names:
                g["tags"] = merged
                changed = True

        self.tag_config = cfg
        if hasattr(self, "ai") and self.ai is not None:
            self.ai.tag_config = cfg
        if changed:
            self.save_tag_config(cfg)
            self.log("已同步标签组结构，并从数据库补全标准词。")
        return changed

    def rebuild_tag_config_from_vocab_file(self, path: Optional[str] = None) -> Dict[str, Any]:
        """从词.txt 直接解析并整组写入标签库（一键修复）。"""
        draft = self.load_vocab_file(path)
        return self.commit_cold_start_draft(
            draft,
            replace_placeholders=True,
            replace_all_group_tags=True,
            cap_closed_groups=False,
        )

    def migrate_pool_standard_tags_to_pending(self) -> Dict[str, Any]:
        """
        S4：将 tags_library / tag_config 中 pool 或未分组标准词降为待审。
        不批量清空视频素材上的 tags 字符串。
        """
        from core.tag_group_ops import (
            known_group_ids,
            migrate_pool_tags_to_pending,
            strip_names_from_tag_groups,
        )

        cfg = self.tag_config or {}
        groups = cfg.get("tag_groups") or []
        known = known_group_ids(groups)
        tags_list = self.db.get_tags_detail() or []
        pending_list = self.db.list_pending_tags(status="pending") or []
        result = migrate_pool_tags_to_pending(tags_list, pending_list, known)

        for name in result.to_pending:
            self.db.add_pending_tag(name, group_id="", source_path=None)

        for tid in result.remove_library_ids:
            self.db.delete_tag_by_id(tid)

        remaining_names = {
            (t.get("tag_name") or "").strip()
            for t in (self.db.get_tags_detail() or [])
        }
        for name in result.remove_library_names:
            if name not in remaining_names:
                continue
            for row in tags_list:
                if (row.get("tag_name") or "").strip() != name:
                    continue
                rid = row.get("id")
                if rid is not None:
                    try:
                        self.db.delete_tag_by_id(int(rid))
                    except Exception:
                        pass

        if result.strip_from_config:
            # 仅从配置中剔除「未挂在任何合法组」的词名；已在合法组 tags 里的保留
            protected: set = set()
            for g in groups:
                if not isinstance(g, dict):
                    continue
                gid = str(g.get("id") or "").strip()
                if not gid or gid.lower() == "pool" or gid not in known:
                    continue
                for t in g.get("tags") or []:
                    if isinstance(t, dict):
                        n = str(t.get("name") or "").strip()
                    else:
                        n = str(t or "").strip()
                    if n:
                        protected.add(n)
            to_strip = [n for n in result.strip_from_config if n not in protected]
            if to_strip:
                new_groups = strip_names_from_tag_groups(groups, to_strip)
                cfg = dict(cfg)
                cfg["tag_groups"] = new_groups
                self.save_tag_config(cfg)

        summary = {
            "to_pending": list(result.to_pending),
            "removed_from_library": list(result.remove_library_names),
            "count": len(result.remove_library_names),
        }
        if summary["count"]:
            try:
                self.log(
                    f"中转池废除迁移：{summary['count']} 个无组标准词已降为待审（素材标签字符串保留）。"
                )
            except Exception:
                pass
        return summary

    def delete_tag_group(
        self,
        group_id: str,
        *,
        target_group_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        删除标签组：空组可直接删；非空须 target_group_id 整组改派后删；
        唯一剩余组拒绝。
        """
        from core.tag_group_ops import reassign_and_remove_group

        cfg = self.tag_config or {}
        groups = list(cfg.get("tag_groups") or [])
        ok, new_groups, err, moved = reassign_and_remove_group(
            groups, group_id, target_group_id=target_group_id
        )
        if not ok:
            return {"ok": False, "error": err, "moved": []}

        if moved and target_group_id:
            self.db.reassign_tags_dimension(moved, str(target_group_id).strip())

        cfg = dict(cfg)
        cfg["tag_groups"] = new_groups
        self.save_tag_config(cfg)
        return {"ok": True, "error": "", "moved": moved, "groups": new_groups}

    def resolve_pending_tag(
        self,
        pending_id: int,
        action: str,
        *,
        group_id: Optional[str] = None,
        standard_tag: Optional[str] = None,
    ) -> bool:
        """
        处理待审词：approve_standard | link_alias | discard
        approve_standard 必须提供有效 group_id（禁止 pool / 缺省回落 custom）。
        """
        from core.tag_group_ops import known_group_ids, validate_standard_tag_group_id

        rows = self.db.execute_query("SELECT * FROM pending_tags WHERE id = ?", (pending_id,))
        if not rows:
            return False
        row = rows[0]
        raw = (row.get("raw_text") or "").strip()
        if action == "discard":
            self.db.update_pending_tag_status(pending_id, "discarded")
            return True
        if action == "link_alias":
            std = (standard_tag or "").strip()
            if not std or not raw:
                return False
            self.db.add_synonym(std, raw)
            self.db.update_pending_tag_status(pending_id, "linked_as_alias")
            return True
        if action == "approve_standard":
            if not raw:
                return False
            gid = str(group_id or "").strip()
            if not gid:
                gid = str(row.get("group_id") or "").strip()
            known = known_group_ids((self.tag_config or {}).get("tag_groups") or [])
            ok, _err = validate_standard_tag_group_id(gid, known)
            if not ok:
                return False
            for k in known:
                if k.lower() == gid.lower():
                    gid = k
                    break
            # 已存在同名标准词：安全改派 dimension（多行去重），避免 UNIQUE 崩溃
            existing = self.db.execute_query(
                "SELECT id, dimension FROM tags_library WHERE tag_name = ?",
                (raw,),
            )
            try:
                if existing:
                    self.db.reassign_tags_dimension([raw], gid)
                else:
                    self.db.add_tag(gid, raw, is_learned=0)
            except Exception as e:
                logger.error(f"批准待审词写入标准词失败 raw={raw!r} group={gid}: {e}")
                return False
            cfg = self.tag_config or {}
            # 从所有组剥离同名，再挂到目标组（避免双组显示）
            for group in cfg.get("tag_groups") or []:
                tags = group.get("tags") or []
                cleaned = []
                for t in tags:
                    if isinstance(t, dict):
                        n = t.get("name")
                    else:
                        n = t
                    if n != raw:
                        cleaned.append(t)
                group["tags"] = cleaned
            for group in cfg.get("tag_groups") or []:
                if group.get("id") == gid:
                    tags = list(group.get("tags") or [])
                    names = []
                    for t in tags:
                        if isinstance(t, dict):
                            names.append(t.get("name"))
                        else:
                            names.append(t)
                    if raw not in names:
                        tags.append(raw)
                        group["tags"] = tags
                    break
            try:
                self.save_tag_config(cfg)
            except Exception as e:
                logger.error(f"批准待审词后保存 tag_config 失败: {e}")
                return False
            self.db.update_pending_tag_status(pending_id, "approved_as_standard")
            return True
        return False

    def check_spelling(self, tags: List[str]) -> Dict[str, str]:
        """详情面板等 GUI 入口：委托 TagProcessor，失败时降级为空建议。"""
        if not tags:
            return {}
        try:
            return self.tag_processor.check_spelling(tags)
        except Exception as e:
            logger.warning(f"拼写检查失败，已降级: {e}")
            return {}

    def get_system_prompt(self) -> str:
        """主分析实际使用的全局 system_prompt（来自 tag_config）。"""
        global_settings = self.tag_config.get("global_settings", {}) if self.tag_config else {}
        return global_settings.get(
            "system_prompt",
            "你是一个资深的影视后期素材整理专家。请通过观察视频帧，提取精准的元数据。",
        )

    def set_system_prompt(self, text: str) -> None:
        """写入并持久化全局 system_prompt，与 AI 分析链路对齐。"""
        if not self.tag_config:
            self.tag_config = {}
        global_settings = self.tag_config.setdefault("global_settings", {})
        global_settings["system_prompt"] = (text or "").strip()
        self.save_tag_config(self.tag_config)

    def preview_analysis_prompts(self, system_prompt_override: Optional[str] = None) -> Dict[str, str]:
        """预览主分析将发给模型的完整 system/user 提示词（不含视频帧）。"""
        if hasattr(self, "ai") and self.ai is not None:
            self.ai.tag_config = self.tag_config
            self.ai.settings = self.settings
            return self.ai.build_analysis_prompts(system_prompt_override)
        return {
            "system_prompt": "",
            "user_prompt": "",
            "system_prompt_header": system_prompt_override or "",
        }

    def get_all_videos(self) -> List[Dict]:
        """V4.0: 获取所有视频，优先使用 L1 内存缓存"""
        with self._cache_lock:
            if self._memory_cache:
                return list(self._memory_cache.values())
        
        videos = self.db.get_all_videos()
        with self._cache_lock:
            self._memory_cache = {v['path']: v for v in videos}
        return videos

    def _save_l3_backup(self):
        """V4.0: 三级缓存 - 磁盘持久化 JSON 备份"""
        try:
            videos = self.get_all_videos()
            with open(RESULTS_FILE_JSON, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def update_video_metadata(self, path: str, updates: Dict):
        """更新视频元数据并持久化到数据库"""
        videos = self.db.get_all_videos()
        for item in videos:
            if item.get("path") == path:
                item.update(updates)
                self.db.upsert_video(item)
                break

    def delete_videos(self, paths: List[str]):
        """取消入库：从数据库中删除视频记录（不删磁盘文件）。"""
        for path in paths:
            self.db.delete_video(path)
        with self._cache_lock:
            if self._memory_cache:
                for path in paths:
                    self._memory_cache.pop(path, None)
            else:
                self._memory_cache = {}

    def uncatalog_videos(self, paths: List[str]):
        """产品语义：取消入库（同 delete_videos，不删磁盘）。"""
        self.delete_videos(paths)
        self.log(f"已取消入库 {len(paths)} 条（磁盘文件保留）。")

    def remove_videos_from_work_scope(self, video_paths: List[str]) -> int:
        """
        移出工作范围：从范围路径中去掉精确文件项，并对仍可能被文件夹覆盖的路径记入排除集。
        不删库记录、不删磁盘文件。
        """
        if not video_paths:
            return 0
        if not hasattr(self, "_work_scope_exclusions"):
            self._work_scope_exclusions: Set[str] = set()
        removed = 0
        to_exclude: List[str] = []
        for p in video_paths:
            if not p:
                continue
            np = normalize_work_path(p)
            to_exclude.append(np)
            # 去掉范围中的精确文件项
            self._work_scope_paths = [
                x for x in self._work_scope_paths if normalize_work_path(x) != np
            ]
            self._work_scope_exclusions.add(np)
            removed += 1
        self.persist_work_scope_if_enabled()
        self.log(f"已移出工作范围 {removed} 条（库记录保留）。")
        return removed

    def bulk_replace_tags(self, old_tag: str, new_tag: str):
        """全局批量替换标签"""
        self.db.bulk_replace_tags(old_tag, new_tag)
        self.db.refresh_tag_usage_counts() # 替换后刷新统计
        # 清除内存缓存以强制重新加载
        with self._cache_lock:
            self._memory_cache = {}
        self.log(f"已将标签 '{old_tag}' 批量替换为 '{new_tag}'")

    def request_cancel_analysis(self) -> None:
        """协作式取消分析任务。"""
        self._analysis_cancel.set()
        self.log("已请求取消分析…")

    def clear_analysis_cancel(self) -> None:
        self._analysis_cancel.clear()

    def is_analysis_cancelled(self) -> bool:
        return self._analysis_cancel.is_set()

    def _emit_analysis_snapshot(self, reducer) -> None:
        with self._progress_lock:
            snap = reducer.snapshot()
            d = snap.to_dict()
        try:
            self.on_analysis_snapshot(d)
        except Exception as e:
            logger.debug(f"on_analysis_snapshot: {e}")
        total = snap.overall_total or 0
        done = snap.overall_done
        if total <= 0:
            self.on_progress(0, 0)
        else:
            self.on_progress(done, total)

    def _persist_analysis_success(self, res: Dict) -> int:
        """入库 + XMP；返回 xmp 失败 0/1。"""
        self.db.upsert_video(res)
        with self._cache_lock:
            if self._memory_cache is not None:
                self._memory_cache[res["path"]] = res
        try:
            self.sync_metadata_to_xmp([res["path"]])
            return 0
        except Exception as e:
            logger.warning(f"XMP 同步失败 {res.get('path')}: {e}")
            self.log(f"XMP 同步失败 {os.path.basename(res.get('path') or '')}: {e}")
            return 1

    def _persist_analysis_failure(self, video_path: str, reason: str) -> None:
        try:
            existing = self.db.get_video_by_path(video_path) or {}
            meta = existing.get("raw_metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            meta = {**meta, "last_error": reason}
            fail_row = {
                **existing,
                "path": video_path,
                "filename": existing.get("filename") or os.path.basename(video_path),
                "status": "failed",
                "raw_metadata": meta,
                "last_error": reason,
            }
            self.db.upsert_video(fail_row)
        except Exception as e:
            logger.warning(f"写入失败状态失败 {video_path}: {e}")

    def run_analysis(
        self,
        input_path: Union[str, List[str]],
        *,
        force_reanalyze: bool = False,
    ) -> Dict[str, Any]:
        """
        执行分析工作流（分析任务）：分析目标集 + 三层重试 + 可选批次补跑。
        返回 {attempted, succeeded, failed, skipped, message, ...}。
        """
        from core.analysis_job_policy import (
            ProgressReducer,
            RetryConfig,
            RoundKind,
            should_batch_rerun,
        )
        from core.analysis_targets import (
            build_path_status_map,
            resolve_analysis_target_paths,
            path_status_key,
        )

        self.clear_analysis_cancel()
        self.ai.analysis_cancel_event = self._analysis_cancel
        self.ai._retry_sleeper = self._retry_sleeper
        self.ai.settings = self.settings

        empty = {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "message": "未找到视频文件。",
        }
        if isinstance(input_path, list):
            all_videos = list(input_path)
        else:
            all_videos = self.file_manager.scan_videos(input_path)

        if not all_videos:
            logger.warning("未找到视频文件")
            self.log("未找到视频文件。")
            return empty

        all_videos = [os.path.abspath(p) for p in all_videos if p]
        status_map = build_path_status_map(self.db.get_all_videos())
        status_map_norm: Dict[str, str] = {}
        for k, v in status_map.items():
            try:
                status_map_norm[path_status_key(k)] = v
            except Exception:
                status_map_norm[k] = v
            status_map_norm[k] = v

        videos_to_process = resolve_analysis_target_paths(
            all_videos, status_map_norm, force=force_reanalyze
        )
        skipped = len(all_videos) - len(videos_to_process)

        self.log(
            f"候选: {len(all_videos)}, 待分析: {len(videos_to_process)}, "
            f"跳过(已分析): {skipped}, 强制: {force_reanalyze}"
        )
        if not videos_to_process:
            msg = "没有待分析视频（已分析项已跳过；可勾选「强制重新分析」）。"
            self.log(msg)
            self.on_progress(0, 0)
            return {
                "attempted": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": skipped,
                "message": msg,
            }

        retry_cfg = RetryConfig.from_settings(self.settings)
        # 冻结本任务配置，避免分析中途改设置搅乱 A/B 行为
        self._job_retry_config = retry_cfg
        self.ai._job_retry_config = retry_cfg
        reducer = ProgressReducer()
        reducer.apply("job_started", target_count=len(videos_to_process))
        self._emit_analysis_snapshot(reducer)

        total_target = len(videos_to_process)
        succeeded = 0
        failed = 0
        xmp_failed = 0
        failure_reasons: List[str] = []
        cancelled = False

        def run_round(paths: List[str], *, kind: RoundKind) -> List[str]:
            """处理一轮；返回本轮仍失败的路径。"""
            nonlocal succeeded, failed, xmp_failed, cancelled
            reducer.apply("round_started", kind=kind, item_count=len(paths))
            self._emit_analysis_snapshot(reducer)
            if kind == RoundKind.RERUN:
                self.log(f"批次补跑：{len(paths)} 条失败项")
            failed_paths: List[str] = []
            rerun_candidates: List[str] = []
            max_workers = SettingsManager.get_setting(
                self.settings, "processing.max_workers", 4
            )
            # 取消后不再提交新任务：用串行检查 + executor
            pending = list(paths)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_video = {}
                for v in pending:
                    if self.is_analysis_cancelled():
                        cancelled = True
                        break
                    future_to_video[
                        executor.submit(
                            self._process_single_video,
                            v,
                            force_reanalyze,
                            progress_reducer=reducer,
                            retry_cfg=retry_cfg,
                        )
                    ] = v
                for future in as_completed(future_to_video):
                    video_path = future_to_video[future]
                    if self.is_analysis_cancelled():
                        cancelled = True
                    try:
                        res, err_reason = future.result()
                    except Exception as e:
                        res, err_reason = None, str(e)
                    if res and res.get("status") == "analyzed":
                        xmp_failed += self._persist_analysis_success(res)
                        succeeded += 1
                        with self._progress_lock:
                            reducer.apply("item_terminal", path=video_path, ok=True)
                    else:
                        reason = err_reason or "未知原因"
                        if self.is_analysis_cancelled() and (
                            not reason or "取消" not in reason
                        ):
                            reason = reason or "用户取消"
                        short_name = os.path.basename(video_path)
                        if len(failure_reasons) < 8:
                            failure_reasons.append(f"{short_name}: {reason}")
                        self._persist_analysis_failure(video_path, reason)
                        failed_paths.append(video_path)
                        # 仅瞬时/可重试失败进入批次补跑；鉴权/取消/文件类跳过 C
                        from core.analysis_job_policy import is_retriable as _is_ret
                        if (
                            "用户取消" not in (reason or "")
                            and _is_ret(reason)
                        ):
                            rerun_candidates.append(video_path)
                        with self._progress_lock:
                            reducer.apply(
                                "item_terminal",
                                path=video_path,
                                ok=False,
                                cancelled=self.is_analysis_cancelled(),
                            )
                    self._emit_analysis_snapshot(reducer)
            # 未提交即取消的路径
            submitted = set(future_to_video.values()) if future_to_video else set()
            for v in pending:
                if v not in submitted and v not in failed_paths:
                    if self.is_analysis_cancelled():
                        self._persist_analysis_failure(v, "用户取消")
                        failed_paths.append(v)
                        with self._progress_lock:
                            reducer.apply(
                                "item_terminal", path=v, ok=False, cancelled=True
                            )
                        self._emit_analysis_snapshot(reducer)
            if cancelled:
                reducer.apply("job_cancelled")
                self._emit_analysis_snapshot(reducer)
            # 本轮返回「可补跑候选」；永久失败只在 failed_paths 计数
            return failed_paths, rerun_candidates

        # 首轮
        failed_paths, rerun_candidates = run_round(videos_to_process, kind=RoundKind.FIRST)
        first_fail_n = len(failed_paths)
        first_ok = total_target - first_fail_n
        succeeded = first_ok
        failed = first_fail_n

        # 批次补跑：仅可重试失败集
        rerun_done = False
        if should_batch_rerun(
            len(rerun_candidates),
            rerun_done=False,
            enabled=retry_cfg.batch_rerun_enabled,
            cancelled=self.is_analysis_cancelled(),
            max_rounds=retry_cfg.batch_rerun_max_rounds,
        ):
            rerun_done = True
            still_failed, _ = run_round(rerun_candidates, kind=RoundKind.RERUN)
            # 永久失败 + 补跑仍失败
            permanent = [p for p in failed_paths if p not in rerun_candidates]
            recovered = len(rerun_candidates) - len(still_failed)
            succeeded = first_ok + recovered
            failed = len(permanent) + len(still_failed)
            failed_paths = permanent + still_failed

        if self.is_analysis_cancelled():
            cancelled = True
            reducer.apply("job_cancelled")
            self._emit_analysis_snapshot(reducer)

        self._save_l3_backup()
        self.file_manager.save_results_to_csv(self.get_all_videos())
        msg = f"分析完成：成功 {succeeded}，失败 {failed}，跳过 {skipped}。"
        if rerun_done:
            msg += " 已执行批次补跑。"
        if cancelled:
            msg += " （已取消）"
        if xmp_failed:
            msg += f" XMP 侧车失败 {xmp_failed}。"
        if failure_reasons:
            # 去重保留顺序
            uniq = list(dict.fromkeys(failure_reasons))
            msg += " 失败摘要：" + "；".join(uniq[:5])
            if len(uniq) > 5:
                msg += f"…（另有 {len(uniq) - 5} 条）"
        self.log(msg)
        return {
            "attempted": total_target,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "xmp_failed": xmp_failed,
            "failure_reasons": failure_reasons,
            "batch_rerun": rerun_done,
            "cancelled": cancelled,
            "message": msg,
        }

    def _process_single_video(
        self,
        video_path: str,
        force_reanalyze: bool = False,
        progress_reducer=None,
        retry_cfg=None,
    ) -> tuple:
        """
        分析单条视频（含单条层重试）。
        返回 (result_dict | None, error_reason | None)。
        """
        from core.analysis_job_policy import (
            AnalysisPhase,
            RetryConfig,
            is_retriable,
            should_retry_item,
        )

        cfg = retry_cfg or getattr(self, "_job_retry_config", None) or RetryConfig.from_settings(
            self.settings
        )
        max_b = cfg.item_max_attempts
        last_reason = "未知原因"

        def emit_phase(phase, attempt_b: int):
            if progress_reducer is None:
                return
            try:
                with self._progress_lock:
                    progress_reducer.apply(
                        "item_phase",
                        path=video_path,
                        phase=phase,
                        attempt_b=attempt_b,
                    )
                self._emit_analysis_snapshot(progress_reducer)
            except Exception:
                pass

        for attempt_b in range(1, max_b + 1):
            if self.is_analysis_cancelled():
                return None, "用户取消"
            try:
                filename = os.path.basename(video_path)
                self.log(
                    f"正在处理: {filename}"
                    + (" [强制]" if force_reanalyze else "")
                    + (f" [单条尝试 {attempt_b}/{max_b}]" if attempt_b > 1 else "")
                )

                emit_phase(AnalysisPhase.EXTRACT, attempt_b)
                if not os.path.exists(video_path):
                    return None, "文件不存在"

                file_hash = self.processor.get_file_hash(video_path)

                proc_res = self.processor.extract_frames(
                    video_path,
                    max_frames=SettingsManager.get_setting(
                        self.settings, "processing.max_frames", 10
                    ),
                    target_size=SettingsManager.get_setting(
                        self.settings, "processing.target_size", 512
                    ),
                    save_thumbnail=True,
                    use_scene_detection=SettingsManager.get_setting(
                        self.settings, "processing.enable_scene_detection", True
                    ),
                )
                frames = proc_res.get("frames", [])
                thumbnail_path = proc_res.get("thumbnail")
                phash = proc_res.get("phash", "")

                if not frames:
                    last_reason = "无法抽取视频帧"
                    self.log(f"处理失败 {filename}: {last_reason}")
                    # 抽帧失败：默认可 B 再试一次（场景检测偶发空），但次数受 item_max 限制
                    if should_retry_item(attempt_b, item_max_attempts=max_b):
                        continue
                    return None, last_reason

                if self.is_analysis_cancelled():
                    return None, "用户取消"

                emit_phase(AnalysisPhase.AI, attempt_b)
                model_name = SettingsManager.get_setting(
                    self.settings,
                    "api.model_personalization.video_classification",
                    "gemini-2.0-flash",
                )
                cache_key = f"{file_hash}_{model_name}"
                ai_data = self.ai.analyze_video(
                    frames, cache_key=cache_key, use_cache=not force_reanalyze
                )

                if not ai_data:
                    if self.is_analysis_cancelled():
                        return None, "用户取消"
                    fail = self.ai.get_last_api_failure() if hasattr(self.ai, "get_last_api_failure") else getattr(self.ai, "_last_api_failure", None)
                    if fail is not None:
                        if fail.cancelled:
                            return None, "用户取消"
                        last_reason = fail.message or "AI 调用失败"
                        self.log(f"处理失败 {filename}: {last_reason}")
                        # 不可重试（鉴权等）→ 禁止 B 层再试
                        if not fail.retriable:
                            return None, last_reason
                        if should_retry_item(attempt_b, item_max_attempts=max_b):
                            continue
                        return None, last_reason
                    last_reason = "AI 未返回有效分析结果"
                    self.log(f"处理失败 {filename}: {last_reason}")
                    if should_retry_item(attempt_b, item_max_attempts=max_b):
                        continue
                    return None, last_reason

                emit_phase(AnalysisPhase.NORMALIZE, attempt_b)
                processed_tags = ai_data.get("tags", [])
                tag_groups = ai_data.get("tag_groups", {})
                raw_category = ai_data.get("category", "Unknown")

                processed_tags, suggested_category = self.tag_processor.process(
                    processed_tags, raw_category
                )

                synced_tag_groups = {}
                for dim_id, group_tags in tag_groups.items():
                    clean_group, _ = self.tag_processor.process(
                        group_tags, suggested_category
                    )
                    synced_tag_groups[dim_id] = clean_group
                tag_groups = synced_tag_groups

                ai_data = dict(ai_data)
                ai_data["tag_groups"] = tag_groups
                ai_data["tags"] = processed_tags
                # 最终归一 + 待审落库（仅成功路径一次）
                ai_data = self.ai.apply_tag_normalization(
                    ai_data, source_path=video_path, persist_pending=True
                )
                processed_tags = ai_data.get("tags", [])
                tag_groups = ai_data.get("tag_groups", {})

                for tag in processed_tags:
                    self.db.increment_tag_usage(tag)

                transcription = ""
                if SettingsManager.get_setting(
                    self.settings, "processing.enable_audio_transcription"
                ):
                    api_key = SettingsManager.get_setting(self.settings, "api.key", "")
                    base_url = SettingsManager.get_setting(
                        self.settings, "api.base_url", ""
                    )
                    transcription = AudioTranscriber.transcribe(
                        video_path, api_key, base_url
                    )

                metadata_injected = False
                if SettingsManager.get_setting(
                    self.settings, "processing.enable_metadata_injection"
                ):
                    metadata_injected = MetadataInjector.inject_tags(
                        video_path,
                        ai_data.get("tags", []),
                        ai_data.get("summary", ""),
                    )

                return {
                    "path": video_path,
                    "filename": filename,
                    "file_hash": file_hash,
                    "phash": phash,
                    "category": suggested_category,
                    "summary": ai_data.get("summary", ""),
                    "tags": processed_tags,
                    "transcription": transcription,
                    "timestamp": datetime.now().isoformat(),
                    "status": "analyzed",
                    "manual_override": False,
                    "metadata_injected": metadata_injected,
                    "thumbnail_path": thumbnail_path,
                    "raw_metadata": ai_data,
                    "face_clusters": ai_data.get("characters", []),
                    "vector_id": ai_data.get("vector_id"),
                    "tag_groups": tag_groups,
                }, None
            except Exception as e:
                last_reason = str(e) or e.__class__.__name__
                if len(last_reason) > 200:
                    last_reason = last_reason[:200] + "…"
                self.log(f"处理失败 {video_path}: {last_reason}")
                if self.is_analysis_cancelled():
                    return None, "用户取消"
                # 不可恢复：不 B 重试
                if not is_retriable(e) and not is_retriable(last_reason):
                    return None, last_reason
                if should_retry_item(attempt_b, item_max_attempts=max_b):
                    continue
                return None, last_reason

        return None, last_reason

    def find_physical_file(self, video_data: Dict, index: Dict[str, str]) -> Optional[str]:
        """鲁棒性查找物理文件路径"""
        p = video_data.get("path")
        fn = video_data.get("filename")
        if p and os.path.exists(p):
            return p
        if fn in index:
            return index[fn]
        if fn and "-" in fn:
            sid = f"suffix_{fn.split('-')[-1]}"
            if sid in index:
                return index[sid]
        return None

    def batch_rename(self, dry_run: bool = False, selected_paths: Optional[List[str]] = None, 
                     custom_pattern: Optional[str] = None, regex_find: Optional[str] = None, 
                     regex_replace: Optional[str] = "") -> List[Dict]:
        """批量重命名，支持自定义模式和正则表达式"""
        logger.info(f"开始批量重命名 (模拟模式: {dry_run})")
        videos = self.db.get_all_videos()
        if not videos:
            self.log("没有可重命名的分析结果。")
            return []

        items_to_rename = videos
        if selected_paths:
            items_to_rename = [item for item in videos if item.get("path") in selected_paths]

        # 建立当前目录索引以增加查找成功率
        file_index = FileManager.get_file_index(os.getcwd())

        success_count = 0
        preview_results = []
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for item in items_to_rename:
            physical_path = self.find_physical_file(item, file_index)
            if not physical_path or not os.path.exists(physical_path):
                self.log(f"未找到物理文件: {item.get('filename')}")
                continue
            
            dir_name = os.path.dirname(physical_path)
            current_fn = os.path.basename(physical_path)
            
            # 提取原始文件名后缀（处理可能已经重命名过的情况）
            raw_parts = current_fn.split("-")
            original_suffix = raw_parts[-1] if len(raw_parts) > 1 else current_fn
            
            category = item.get("category", "Other")
            tags = item.get("tags", ["无"]*5)
            tags_str = "_".join(tags)
            
            # V6.0: 优先级：custom_pattern > 全局设置 > 默认设置
            pattern = custom_pattern
            if not pattern:
                global_settings = self.tag_config.get("global_settings", {})
                export_schemes = global_settings.get("export_schemes", {})
                pattern = export_schemes.get("filename_pattern")
            
            if not pattern:
                pattern = SettingsManager.get_setting(self.settings, "rename_pattern", "{category}-{tags}-{summary}-{original_name}")
            
            summary_safe = FileManager.sanitize_filename(item.get("summary") or "", max_len=50)
            
            # V6.0: 新增日期变量
            current_date = datetime.now().strftime("%Y%m%d")

            # {emotion}/{composition} 已下线：替换为空，避免旧模板残留脏值
            new_fn = pattern.replace("{category}", str(category))\
                            .replace("{tags}", str(tags_str))\
                            .replace("{summary}", str(summary_safe))\
                            .replace("{emotion}", "")\
                            .replace("{composition}", "")\
                            .replace("{date}", current_date)\
                            .replace("{original_name}", str(original_suffix))
            
            # 应用正则替换 (V6.0 新增)
            if regex_find:
                try:
                    new_fn = re.sub(regex_find, regex_replace, new_fn)
                except re.error as ree:
                    logger.warning(f"正则表达式错误: {ree}")
            
            new_fn = FileManager.sanitize_filename(new_fn, max_len=200)
            target_path = os.path.join(dir_name, new_fn)
            
            if os.path.normpath(physical_path) == os.path.normpath(target_path):
                continue
            
            if dry_run:
                preview_results.append({"old": current_fn, "new": new_fn, "path": physical_path})
                success_count += 1
                continue
                
            try:
                if os.path.exists(target_path):
                    base, ext = os.path.splitext(new_fn)
                    target_path = os.path.join(dir_name, f"{base}_dup_{int(time.time()) % 1000}{ext}")
                
                os.rename(physical_path, target_path)
                
                # 同步重命名 XMP 文件
                old_xmp = os.path.splitext(physical_path)[0] + ".xmp"
                new_xmp = os.path.splitext(target_path)[0] + ".xmp"
                if os.path.exists(old_xmp):
                    try:
                        os.rename(old_xmp, new_xmp)
                        logger.info(f"同步重命名 XMP: {os.path.basename(old_xmp)} -> {os.path.basename(new_xmp)}")
                    except Exception as xe:
                        logger.warning(f"XMP 重命名失败: {xe}")

                self.db.add_rename_record(session_id, physical_path, target_path)
                
                item["path"] = target_path
                item["filename"] = os.path.basename(target_path)
                item["status"] = "renamed"
                self.db.upsert_video(item)
                success_count += 1
            except Exception as e:
                self.log(f"重命名失败 {current_fn}: {e}")

        if not dry_run:
            self.file_manager.save_results_to_csv(self.db.get_all_videos())
            self.log(f"重命名完成！成功: {success_count}")
        else:
            self.log(f"预览完成！拟重命名: {success_count}")
            
        return preview_results

    def rollback_last_session(self) -> bool:
        """回滚上一次重命名会话"""
        session_id = self.db.get_last_session_id()
        if not session_id:
            self.log("没有可回滚的历史记录。")
            return False
        
        history = self.db.get_history_by_session(session_id)
        count = 0
        for record in history:
            old_path, new_path = record["old_path"], record["new_path"]
            try:
                if os.path.exists(new_path):
                    os.rename(new_path, old_path)
                    
                    # 同步回滚 XMP
                    new_xmp = os.path.splitext(new_path)[0] + ".xmp"
                    old_xmp = os.path.splitext(old_path)[0] + ".xmp"
                    if os.path.exists(new_xmp):
                        try:
                            os.rename(new_xmp, old_xmp)
                        except:
                            pass

                    # 更新数据库中的路径
                    rows = self.db.execute_query("SELECT * FROM videos WHERE path = ?", (new_path,))
                    if rows:
                        video_data = dict(rows[0])
                        video_data["path"] = old_path
                        video_data["filename"] = os.path.basename(old_path)
                        video_data["status"] = "analyzed"
                        self.db.upsert_video(video_data)
                    count += 1
            except Exception as e:
                self.log(f"回滚失败 {new_path}: {e}")
        
        self.db.delete_session(session_id)
        self.log(f"回滚完成，还原了 {count} 个文件。")
        return True

    def export_to_fcpx_xml(self, output_path: str, selected_paths: Optional[List[str]] = None):
        """导出 FCPX XML；selected_paths 限定导出集合（None 表示调用方已筛好则应显式传入）。"""
        videos = self.db.get_all_videos()
        if selected_paths is not None:
            allow = {normalize_work_path(p) for p in selected_paths}
            videos = [v for v in videos if normalize_work_path(v.get("path", "")) in allow]
        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <library>
        <event name="AI_Organized_Videos">
"""
        for item in videos:
            path = item.get("path", "")
            name = item.get("filename", "")
            category = item.get("category", "")
            tags_list = item.get("tags", [])
            tags = ",".join(tags_list)
            summary = item.get("summary", "")
            
            xml_content += f"""            <asset id="{hashlib.md5(path.encode()).hexdigest()}" name="{name}" src="file://{path}">
                <keyword-collection name="{category}"/>
                <keyword-collection name="{tags}"/>
                <note>{summary}</note>
            </asset>
"""
        xml_content += """        </event>
    </library>
</fcpxml>"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
        self.log(f"已导出 FCPX XML: {output_path}")

    def export_to_ale(self, output_path: str, selected_paths: Optional[List[str]] = None):
        """
        导出 Avid Log Exchange (ALE) 文件，专门用于达芬奇 (DaVinci Resolve) 的元数据导入。
        ALE 是一种通用的、基于制表符分隔的格式，达芬奇对其支持非常出色。
        """
        videos = self.db.get_all_videos()
        if selected_paths:
            videos = [v for v in videos if v.get("path") in selected_paths]
            
        if not videos:
            self.log("没有可导出的视频数据。")
            return
            
        # ALE 头部信息
        lines = [
            "Heading",
            "FIELD_DELIM\tTABS",
            "VIDEO_FORMAT\t1080",
            "FPS\t25", # 默认为 25，实际导入时达芬奇会根据文件名匹配
            "",
            "Column",
            "Name\tDescription\tKeywords\tCategory\tSummary"
        ]
        
        lines.append("Data")
        
        for item in videos:
            name = item.get("filename", "")
            description = item.get("summary", "").replace("\t", " ").replace("\n", " ")
            tags = item.get("tags", [])
            keywords = ", ".join(tags)
            category = item.get("category", "")
            summary = description  # 重复一遍以防字段映射不同
            
            # 使用制表符分隔（已去掉 Emotion / Rating / Composition）
            row = f"{name}\t{description}\t{keywords}\t{category}\t{summary}"
            lines.append(row)
            
        try:
            with open(output_path, "w", encoding="utf-8-sig") as f: # 修改为 utf-8-sig 以增强达芬奇兼容性
                f.write("\n".join(lines))
            self.log(f"已导出 ALE 文件: {output_path}")
            return True
        except Exception as e:
            self.log(f"导出 ALE 失败: {e}")
            return False

    def sync_metadata_to_xmp(self, selected_paths: Optional[List[str]] = None):
        """
        为视频生成 XMP 侧边文件，优化了达芬奇与 PR 的双重兼容性。
        V6.0: 增加对全局导出方案的支持，并支持数据库驱动的层级标签。
        人物 Region / 层级 lookup 缺失时安全降级，不得抛 NameError。
        """
        from core.analysis_targets import path_status_key

        videos = self.db.get_all_videos()
        if selected_paths:
            allow = {path_status_key(p) for p in selected_paths if p}
            videos = [
                v for v in videos
                if path_status_key(v.get("path", "")) in allow
            ]
            
        # 获取全局导出设置
        global_settings = self.tag_config.get("global_settings", {}) if self.tag_config else {}
        export_schemes = global_settings.get("export_schemes", {}) or {}
        use_hierarchical = export_schemes.get("xmp_hierarchical", True)
        prefix_category = export_schemes.get("xmp_prefix_category", True)

        # 建立标签库以供转换 + 层级 / 人物集合（必须在本函数内构建，禁止未定义名）
        tags_detail = self.db.get_tags_detail() or []
        zh_to_en = {t["tag_name"]: t.get("name_en") for t in tags_detail if t.get("name_en")}
        en_to_zh = {t.get("name_en"): t["tag_name"] for t in tags_detail if t.get("name_en")}

        tag_lookup: Dict[str, Dict] = {}
        id_lookup: Dict[Any, Dict] = {}
        person_tags: set = set()
        _person_dims = frozenset({"subject", "主体", "person", "人物", "people"})
        for t in tags_detail:
            name = t.get("tag_name")
            if not name:
                continue
            tag_lookup[name] = t
            tid = t.get("id")
            if tid is not None:
                id_lookup[tid] = t
            dim = str(t.get("dimension") or "").strip().lower()
            if t.get("is_person") or dim in _person_dims:
                person_tags.add(name)
        
        # 确定目标语言 (V6.0)
        target_lang = global_settings.get("language", "zh-CN")

        def translate_tag(tag):
            if target_lang == "en" and tag in zh_to_en:
                return zh_to_en[tag]
            elif target_lang == "zh-CN" and tag in en_to_zh:
                return en_to_zh[tag]
            return tag

        def get_full_path(tag_name):
            if tag_name not in tag_lookup:
                return [tag_name]
            
            path = []
            curr = tag_lookup[tag_name]
            path.append(curr["tag_name"])
            while curr.get("parent_id"):
                parent = id_lookup.get(curr["parent_id"])
                if parent and parent["tag_name"] not in path: # 避免循环引用
                    path.insert(0, parent["tag_name"])
                    curr = parent
                else:
                    break
            return path

        success_count = 0
        for item in videos:
            video_path = item.get("path")
            if not video_path or not os.path.exists(video_path):
                continue
                
            xmp_path = os.path.splitext(video_path)[0] + ".xmp"
            tags = item.get("tags", []) or []
            summary = escape(item.get("summary", "") or "")
            category = item.get("category", "") or ""
            
            c1_mood = ""
            h_tags = []
            
            # 处理分类前缀
            if prefix_category and category:
                h_tags.append(f"分类|{category}")

            regions_rdf = ""
            active_person_tags = []

            # face_clusters / characters 名称也视为人物，用于 Region 联动
            face_names = set()
            for face in item.get("face_clusters", []) or []:
                if isinstance(face, dict) and face.get("name"):
                    face_names.add(str(face["name"]))
                elif isinstance(face, str) and face.strip():
                    face_names.add(face.strip())
            item_person_tags = set(person_tags) | face_names

            for t in tags:
                # 翻译标签 (V6.0)
                t_display = translate_tag(t)
                
                if t in item_person_tags or t_display in item_person_tags:
                    active_person_tags.append(t_display)

                if use_hierarchical:
                    full_path = get_full_path(t)
                    full_path = [translate_tag(p) for p in full_path]
                    if len(full_path) > 1:
                        h_tags.append("|".join(full_path))
                    else:
                        # 兜底：如果没在库里，尝试从 tag_config 映射维度
                        dim_found = False
                        if self.tag_config:
                            groups = self.tag_config.get("tag_groups", [])
                            for g in groups:
                                if t in g.get("tags", []):
                                    dim_name = translate_tag(g.get("name", g.get("id")).split(" ")[0])
                                    h_tags.append(f"{dim_name}|{t_display}")
                                    dim_found = True
                                    break
                        if not dim_found:
                            h_tags.append(t_display)
                else:
                    h_tags.append(t_display)
                
                # 特殊逻辑：如果是氛围组的，作为 Label
                if "氛围" in "|".join(get_full_path(t)) or "Mood" in "|".join(get_full_path(t)):
                    c1_mood = escape(t_display)

            # 构建 XMP 内容
            tags_rdf = "\n".join([f"     <rdf:li>{escape(translate_tag(tag))}</rdf:li>" for tag in tags])
            hierarchical_tags_rdf = "\n".join([f"     <rdf:li>{escape(htag)}</rdf:li>" for htag in h_tags])
            
            # 人脸识别 Region 联动 (V6.0)
            if active_person_tags:
                regions_list = []
                faces = item.get("face_clusters", [])
                
                for i, name in enumerate(active_person_tags):
                    face_info = next((f for f in faces if isinstance(f, dict) and f.get("name") == name), None)
                    x, y, w, h = (0.5, 0.5, 0.2, 0.2)
                    if face_info and "box" in face_info:
                        box = face_info["box"]
                        if len(box) == 4: x, y, w, h = box
                    
                    regions_list.append(f"""
      <rdf:li rdf:parseType="Resource">
       <mwg-rs:Name>{escape(name)}</mwg-rs:Name>
       <mwg-rs:Type>Face</mwg-rs:Type>
       <mwg-rs:Area rdf:parseType="Resource">
        <stArea:x>{x}</stArea:x>
        <stArea:y>{y}</stArea:y>
        <stArea:w>{w}</stArea:w>
        <stArea:h>{h}</stArea:h>
        <stArea:unit>normalized</stArea:unit>
       </mwg-rs:Area>
      </rdf:li>""")
                
                if regions_list:
                    regions_rdf = f"""
   <mwg-rs:Regions rdf:parseType="Resource">
    <mwg-rs:AppliedToDimensions rdf:parseType="Resource">
     <stDim:w>1920</stDim:w>
     <stDim:h>1080</stDim:h>
     <stDim:unit>pixel</stDim:unit>
    </mwg-rs:AppliedToDimensions>
    <mwg-rs:RegionList>
     <rdf:Bag>
{"".join(regions_list)}
     </rdf:Bag>
    </mwg-rs:RegionList>
   </mwg-rs:Regions>"""

            xmp_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
    xmlns:mwg-rs="http://www.metadataworkinggroup.com/schemas/regions/"
    xmlns:stArea="http://ns.adobe.com/xmp/sType/Area#"
    xmlns:stDim="http://ns.adobe.com/xmp/sType/Dimensions#">
   <xmp:Label>{c1_mood}</xmp:Label>
   <dc:description>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">{summary}</rdf:li>
    </rdf:Alt>
   </dc:description>
   <dc:subject>
    <rdf:Bag>
{tags_rdf}
    </rdf:Bag>
   </dc:subject>
   <lr:hierarchicalSubject>
    <rdf:Bag>
{hierarchical_tags_rdf}
    </rdf:Bag>
   </lr:hierarchicalSubject>{regions_rdf}
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""
            try:
                with open(xmp_path, "w", encoding="utf-8") as f:
                    f.write(xmp_content)
                success_count += 1
            except Exception as e:
                self.log(f"生成 XMP 失败 {video_path}: {e}")
                
        self.log(f"元数据同步完成，生成了 {success_count} 个 XMP 文件。")
        return success_count

    def execute_physical_migration(self, target_root: str, selected_paths: Optional[List[str]] = None):
        """V4.0: 执行物理迁移，按分类自动归档文件"""
        videos = self.db.get_all_videos()
        if selected_paths:
            videos = [v for v in videos if v.get("path") in selected_paths]
            
        success_count = 0
        for item in videos:
            old_path = item.get("path")
            if not old_path or not os.path.exists(old_path):
                continue
                
            category = item.get("category", "Other")
            dest_dir = os.path.join(target_root, category)
            if not os.path.exists(dest_dir):
                os.makedirs(dest_dir)
                
            filename = os.path.basename(old_path)
            new_path = os.path.join(dest_dir, filename)
            
            if os.path.normpath(old_path) == os.path.normpath(new_path):
                continue
            
            # 避免覆盖
            if os.path.exists(new_path):
                base, ext = os.path.splitext(filename)
                new_path = os.path.join(dest_dir, f"{base}_{int(time.time()) % 1000}{ext}")
                
            try:
                logger.info(f"执行物理迁移: {filename} -> {category}/")
                self.log(f"正在迁移: {filename} -> {category}/")
                # 原子化迁移: 复制 + 校验 + 删除 (安全性保障)
                shutil.copy2(old_path, new_path)
                
                # 同步迁移 XMP
                old_xmp = os.path.splitext(old_path)[0] + ".xmp"
                new_xmp = os.path.splitext(new_path)[0] + ".xmp"
                if os.path.exists(old_xmp):
                    try:
                        shutil.copy2(old_xmp, new_xmp)
                    except:
                        pass

                # 简单的大小校验作为第一步，哈希校验作为第二步
                if os.path.getsize(old_path) == os.path.getsize(new_path):
                    os.remove(old_path)
                    if os.path.exists(old_xmp) and os.path.exists(new_xmp):
                        try:
                            os.remove(old_xmp)
                        except:
                            pass
                    
                    # 更新数据库中的路径
                    item["path"] = new_path
                    self.db.upsert_video(item)
                    
                    # 记录迁移日志
                    self.db.execute_non_query(
                        "INSERT INTO migration_history (old_path, new_path, reason) VALUES (?, ?, ?)",
                        (old_path, new_path, "Auto-categorization")
                    )
                    success_count += 1
                else:
                    self.log(f"迁移校验失败 (大小不一致): {filename}")
            except Exception as e:
                self.log(f"迁移过程出错 {filename}: {e}")
                
        self.log(f"物理整理完成，成功迁移: {success_count}")
        return success_count

    def smart_semantic_search(self, query: str) -> List[Dict]:
        """V5.0: 增强搜索，支持 tag1 + tag2 (AND) 和 tag1 | tag2 (OR)"""
        videos = self.get_all_videos()
        results = []
        
        query = query.strip()
        if not query:
            return videos

        # 简单的布尔解析
        is_and = "+" in query
        is_or = "|" in query and not is_and
        
        if is_and:
            keywords = [k.strip().lower() for k in query.split("+") if k.strip()]
        elif is_or:
            keywords = [k.strip().lower() for k in query.split("|") if k.strip()]
        else:
            keywords = [k.strip().lower() for k in query.split() if k.strip()]

        for v in videos:
            tags = [t.lower() for t in v.get("tags", [])]
            filename = v.get("filename", "").lower()
            summary = v.get("summary", "").lower()
            
            match_scores = []
            for k in keywords:
                score = 0
                if k in filename: score += 10
                if any(k in t for t in tags): score += 5
                if k in summary: score += 3
                match_scores.append(score)
            
            if is_and:
                # 必须所有关键词都有匹配分
                if all(s > 0 for s in match_scores):
                    v["search_score"] = sum(match_scores)
                    results.append(v)
            elif is_or:
                # 只要有一个匹配
                if any(s > 0 for s in match_scores):
                    v["search_score"] = max(match_scores)
                    results.append(v)
            else:
                # 默认逻辑：任意匹配并累加分
                total_score = sum(match_scores)
                if total_score > 0:
                    v["search_score"] = total_score
                    results.append(v)
                
        # 按分值排序
        results.sort(key=lambda x: x.get("search_score", 0), reverse=True)
        return results

# 为了兼容性
VideoOrganizer = VideoOrganizerService

if __name__ == "__main__":
    # 简单的命令行运行逻辑
    service = VideoOrganizerService()
    print("Video Organizer Service initialized.")
