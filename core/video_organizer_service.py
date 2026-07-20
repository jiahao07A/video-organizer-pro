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

SETTINGS_FILE = get_resource_path("settings.json")
RESULTS_FILE_JSON = "video_analysis_results.json"
RESULTS_FILE_CSV = "video_analysis_results.csv"
BACKUP_DIR = "backups"
THUMBNAILS_DIR = ".thumbnails"
DB_FILE = "video_organizer.db"
ENV_FILE = get_resource_path(".env")
DICTIONARY_FILE = get_resource_path("词.txt")
TAG_CONFIG_FILE = "tag_config.json"

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
            video_data.get("emotion"),
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
        d["composition"] = None
        d["rating"] = 0
        d["quality_score"] = None
        d["is_proxy_needed"] = 0
        d["tag_weights"] = {}
        d["thumbnail_path"] = d["thumbnail"]
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
            d["composition"] = None
            d["rating"] = 0
            d["quality_score"] = None
            d["is_proxy_needed"] = 0
            d["tag_weights"] = {}
                
            d["thumbnail_path"] = d["thumbnail"]
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
        """数据库级批量替换视频标签"""
        videos = self.get_all_videos()
        for video in videos:
            tags = video.get("tags", [])
            if old_tag in tags:
                new_tags = [new_tag if t == old_tag else t for t in tags]
                # 去重
                new_tags = list(dict.fromkeys(new_tags))
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

class SettingsManager:
    """管理配置信息，支持从 SQLite 数据库或 settings.json 加载，支持嵌套键名访问"""
    
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
        """加载配置，优先使用 settings.json，然后合并数据库中的配置"""
        settings = DEFAULT_SETTINGS.copy()
        
        # 1. 尝试从文件加载
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    file_settings = json.load(f)
                    # 深度更新 settings
                    def deep_update(d, u):
                        for k, v in u.items():
                            if isinstance(v, dict):
                                d[k] = deep_update(d.get(k, {}), v)
                            else:
                                d[k] = v
                        return d
                    deep_update(settings, file_settings)
            except Exception:
                pass

        # 2. 如果提供了数据库，同步到数据库（主要是为了向后兼容）
        if db:
            try:
                # 这里简单处理，将整个 JSON 存入数据库的一个字段，或者按原样存储
                # 考虑到复杂嵌套，目前倾向于保持文件作为主要来源
                pass
            except Exception:
                pass
                
        return settings

    @staticmethod
    def save_settings(settings: Dict, db: Optional[DatabaseManager] = None):
        """保存配置到文件和数据库"""
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        
        if db:
            # 兼容旧版本：如果是扁平键，存入数据库
            for k, v in settings.items():
                if not isinstance(v, dict):
                    db.save_setting(k, v)

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
                        phash = cls.calculate_phash(frame)
                        
                        if save_thumbnail:
                            if not os.path.exists(THUMBNAILS_DIR):
                                os.makedirs(THUMBNAILS_DIR)
                            file_hash = hashlib.md5(video_path.encode()).hexdigest()
                            # V4.0: 使用 WebP 进行极致压缩 (三级缓存之磁盘缩略图)
                            thumbnail_path = os.path.join(THUMBNAILS_DIR, f"{file_hash}.webp")
                            try:
                                # 尝试使用 WebP，如果不支持则退回到 JPEG
                                cv2.imwrite(thumbnail_path, frame, [int(cv2.IMWRITE_WEBP_QUALITY), 80])
                            except:
                                thumbnail_path = os.path.join(THUMBNAILS_DIR, f"{file_hash}.jpg")
                                cv2.imwrite(thumbnail_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    resized_frame = cls.resize_image(frame_rgb, target_size)
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
            api_key=api_key,
            base_url=base_url,
            max_retries=2,
            timeout=45.0
        )
        
        # 模型路由 - 动态获取，不再缓存到成员变量
        self.tag_lib_lock = threading.Lock()
        
        # 初始化标签库
        self.init_tag_libraries()

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
        logger.info(f"正在调用 AI 模型: {model} (JSON 模式: {json_mode})")
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
            
            if json_mode:
                data = json.loads(res_content) if res_content else None
                logger.debug(f"AI 响应解析成功")
                return data
            return res_content
        except Exception as e:
            logger.error(f"AI API 调用出错: {e}")
            return None

    def build_analysis_prompts(self, system_prompt_override: Optional[str] = None) -> Dict[str, str]:
        """
        组装主分析真正发给模型的 system / user 提示词（不含视频帧）。
        供 analyze_video 与设置页预览共用。
        """
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
        dimension_rules = []
        # ADR-0002：分析结果只保留分类/摘要/情绪/标签，不再要求评分类字段
        expected_json_structure = {
            "category": f"从 {categories_list} 中选择一个最合适的视频大类",
            "summary": "50字以内的核心内容摘要",
            "emotion": "识别视频的情感基调",
        }

        for i, group in enumerate(tag_groups, 1):
            dim_id = group.get("id")
            name = group.get("name", dim_id)
            rules = group.get("rules", {})
            selection_mode = rules.get("selection_mode", "single")
            max_count = rules.get("max_count", 1)
            expandable = rules.get("ai_expandable", False)
            local_prompt = rules.get("local_prompt", "")
            tags_pool = group.get("tags", [])
            pool_names = []
            for t in tags_pool:
                if isinstance(t, dict):
                    n = str(t.get("name", "")).strip()
                else:
                    n = str(t).strip()
                if n:
                    pool_names.append(n)

            rule = f"{i}. **{name}** ({dim_id}): {local_prompt}\n"
            mode_desc = "单选" if selection_mode == "single" else "多选"
            rule += f"   - 约束：{mode_desc}，最大数量 {max_count}。\n"
            if pool_names:
                if not expandable:
                    rule += f"   - 库约束：必须严格从以下预设库中选择：[{', '.join(pool_names)}]\n"
                else:
                    rule += f"   - 库参考：优先从预设库选择，也可自行补充：[{', '.join(pool_names)}]\n"
            else:
                rule += "   - 约束：请根据视频内容自由生成。\n"
            dimension_rules.append(rule)

            if selection_mode == "single" and max_count == 1:
                expected_json_structure[dim_id] = "选中的单个标签字符串"
            else:
                expected_json_structure[dim_id] = ["选中的标签列表"]

        rules_str = "\n".join(dimension_rules) if dimension_rules else "（当前未配置任何标签组；请到标签库添加 tag_groups）"
        json_template = json.dumps(expected_json_structure, ensure_ascii=False, indent=2)
        system_prompt = f"{system_prompt_header.strip()}\n你必须返回一个严格符合给定结构的有效 JSON 对象。"
        user_prompt = f"""请通过观察视频帧分析其内容，并严格遵守以下规则输出 JSON：

### 1. 分类与标签规则
{rules_str}

### 2. 输出格式要求
你必须返回一个 JSON 对象，包含以下字段：
{json_template}

### 3. 注意事项
1. 结果必须是合法 JSON 格式，不要包含 Markdown 代码块标记（除非接口要求）。
2. 对于禁止新增标签的组，若库中无完全匹配项，请选择语意最接近的一个。
3. 摘要需客观描述画面，避免主观臆断。
4. 不要输出构图、星级、质量分、代理建议或标签权重等字段。
"""
        return {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "system_prompt_header": system_prompt_header.strip(),
        }

    def analyze_video(self, base64_frames: List[str], cache_key: Optional[str] = None) -> Optional[Dict]:
        if not base64_frames:
            return None

        if self.db and cache_key:
            cached = self.db.get_cache(cache_key)
            if cached:
                logger.info("使用 API 响应缓存")
                return cached

        # 与设置页预览共用同一条组装链路
        prompts = self.build_analysis_prompts()
        system_prompt = prompts["system_prompt"]
        user_prompt = prompts["user_prompt"]
        tag_groups = (self.tag_config or {}).get("tag_groups", [])

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
            # 4. 结果校验与清洗 (V6.0)
            final_tags = []
            final_tag_groups = {}
            
            for group in tag_groups:
                dim_id = group.get("id")
                rules = group.get("rules", {})
                selection_mode = rules.get("selection_mode", "single")
                max_count = rules.get("max_count", 1)
                tags_pool = group.get("tags", [])
                expandable = rules.get("ai_expandable", False)
                
                val = data.get(dim_id)
                if not val:
                    continue
                
                # 统一转为列表处理
                if isinstance(val, str):
                    current_tags = [val]
                elif isinstance(val, list):
                    current_tags = val
                else:
                    current_tags = []
                
                valid_tags = []
                for t in current_tags:
                    t = str(t).strip()
                    if not t: continue
                    
                    if not expandable and tags_pool:
                        # 必须在库中 (模糊匹配)
                        if t not in tags_pool:
                            best_match = self._simple_fuzzy_match(t, tags_pool)
                            if best_match:
                                t = best_match
                            else:
                                continue # 跳过不合规标签
                    valid_tags.append(t)
                
                # 强制执行单选/多选及数量限制
                if selection_mode == "single":
                    valid_tags = valid_tags[:1]
                else:
                    valid_tags = valid_tags[:max_count]
                
                # 存入结果
                final_tag_groups[dim_id] = valid_tags
                final_tags.extend(valid_tags)
                
                # AI 扩展逻辑
                if expandable and self.db:
                    for t in valid_tags:
                        if t not in tags_pool:
                            self.db.add_tag(dim_id, t, is_learned=1)

            data["tags"] = final_tags
            data["tag_groups"] = final_tag_groups
            data["tags_zh"] = final_tags # 保持向后兼容

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

    def _simple_fuzzy_match(self, tag: str, pool: List[str]) -> Optional[str]:
        """简单的字符串模糊匹配"""
        if not pool: return None
        # 1. 精确匹配
        if tag in pool: return tag
        # 2. 包含匹配
        for p in pool:
            if tag in p or p in tag:
                return p
        return None

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
        self.processor = VideoProcessor()
        self.ai = AIHandler(self.settings, self.db, self.tag_config)
        self.tag_processor = TagProcessor(self.settings, self.db)
        json_path = results_json if results_json is not None else RESULTS_FILE_JSON
        csv_path = results_csv if results_csv is not None else RESULTS_FILE_CSV
        self.file_manager = FileManager(self.db, json_path, csv_path)
        
        # V4.0: L1 内存缓存 (三级缓存架构之一)
        self._memory_cache = {}
        self._cache_lock = threading.Lock()
        # 工作范围：当前工作台呈现与处理的路径集合（文件 + 文件夹）
        self._work_scope_paths: List[str] = []

    # --- 工作范围 (Work Scope) ---

    def get_work_scope_paths(self) -> List[str]:
        """返回当前工作范围路径列表（副本）。"""
        return list(self._work_scope_paths)

    def set_work_scope_paths(self, paths: List[str], *, scan: bool = True) -> Dict[str, Any]:
        """
        整份替换工作范围。
        scan=True 时扫描范围内视频并入库：新文件登记为未分析(pending)，已存在不覆盖分析结果。
        """
        self._work_scope_paths = dedupe_scope_paths(paths)
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
        return self.set_work_scope_paths(paths, scan=scan)

    def append_work_scope(self, paths: List[str], *, scan: bool = True) -> Dict[str, Any]:
        """累加路径到当前工作范围（去重），可选扫盘入库。"""
        combined = list(self._work_scope_paths) + list(paths or [])
        return self.set_work_scope_paths(combined, scan=scan)

    def is_path_in_work_scope(self, video_path: str) -> bool:
        """视频路径是否落在当前工作范围内。"""
        return is_video_path_in_scope(video_path, self._work_scope_paths)

    def resolve_operation_target_paths(
        self, checked_paths: Optional[List[str]] = None
    ) -> List[str]:
        """
        解析工作台批量操作目标路径。
        - 工作范围为空 → []
        - 有勾选 → 勾选 ∩ 工作范围
        - 无勾选 → 工作范围内全部已入库路径
        绝不回退到全库。
        """
        if not self._work_scope_paths:
            return []
        in_scope = self.get_videos_in_work_scope()
        scope_paths = [v.get("path") for v in in_scope if v.get("path")]
        if not checked_paths:
            return list(scope_paths)
        scope_norm = {normalize_work_path(p) for p in scope_paths}
        out: List[str] = []
        seen = set()
        for p in checked_paths:
            if not p:
                continue
            np = normalize_work_path(p)
            if np in scope_norm and np not in seen:
                # 用库内原始 path 字符串
                for sp in scope_paths:
                    if normalize_work_path(sp) == np:
                        out.append(sp)
                        seen.add(np)
                        break
        return out

    def get_videos_for_operation(
        self, checked_paths: Optional[List[str]] = None
    ) -> List[Dict]:
        """工作台操作目标视频记录（⊆ 工作范围）。"""
        targets = set(normalize_work_path(p) for p in self.resolve_operation_target_paths(checked_paths))
        if not targets:
            return []
        return [
            v
            for v in self.get_videos_in_work_scope()
            if normalize_work_path(v.get("path", "")) in targets
        ]

    def persist_work_scope_if_enabled(self) -> None:
        """若开启「记住上次工作范围」，把当前路径写入 settings。"""
        prefs = self.settings.setdefault("ui_preferences", {})
        if not prefs.get("remember_work_scope"):
            return
        prefs["last_work_scope"] = list(self._work_scope_paths)
        try:
            SettingsManager.save_settings(self.settings, self.db)
        except Exception as e:
            logger.warning(f"保存工作范围失败: {e}")

    def restore_work_scope_if_enabled(self) -> bool:
        """启动时若开启记住范围则恢复；成功返回 True。"""
        prefs = self.settings.get("ui_preferences", {}) or {}
        if not prefs.get("remember_work_scope"):
            return False
        paths = prefs.get("last_work_scope") or []
        if not paths:
            return False
        self.set_work_scope_paths(list(paths), scan=True)
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
                    "emotion": None,
                    "tag_groups": {},
                }
                if self.db.insert_video_if_absent(record):
                    registered.append(abs_path)
                    with self._cache_lock:
                        if self._memory_cache is not None:
                            # 仅在缓存已预热时追加，避免半缓存状态
                            if self._memory_cache:
                                self._memory_cache[abs_path] = {
                                    **record,
                                    "thumbnail_path": None,
                                    "thumbnail": None,
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
        self.persist_work_scope_if_enabled()

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
                    "ale_columns": ["Name", "Keywords", "Category", "Summary", "Emotion"]
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
            return default_v6

        try:
            with open(TAG_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            # 检查版本并执行迁移
            version = str(config.get("version", "1.0"))
            if version < "6.0":
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
                self.save_tag_config(migrated)
                return migrated
            
            return config
        except Exception as e:
            logger.error(f"加载标签配置失败: {e}")
            return default_v6

    def save_tag_config(self, config: Dict):
        self.tag_config = config
        try:
            with open(TAG_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            # 更新 AI Handler 中的配置
            if hasattr(self, "ai"):
                self.ai.tag_config = config
        except Exception as e:
            logger.error(f"保存标签配置失败: {e}")

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
        """从数据库中删除视频记录"""
        for path in paths:
            self.db.delete_video(path)

    def bulk_replace_tags(self, old_tag: str, new_tag: str):
        """全局批量替换标签"""
        self.db.bulk_replace_tags(old_tag, new_tag)
        self.db.refresh_tag_usage_counts() # 替换后刷新统计
        # 清除内存缓存以强制重新加载
        with self._cache_lock:
            self._memory_cache = {}
        self.log(f"已将标签 '{old_tag}' 批量替换为 '{new_tag}'")

    def run_analysis(self, input_path: Union[str, List[str]]):
        """执行分析工作流"""
        if isinstance(input_path, list):
            all_videos = input_path
        else:
            all_videos = self.file_manager.scan_videos(input_path)
            
        if not all_videos:
            logger.warning("未找到视频文件")
            self.log("未找到视频文件。")
            return

        current_videos = self.db.get_all_videos()
        processed_paths = {r['path'] for r in current_videos}
        
        videos_to_process = [v for v in all_videos if v not in processed_paths]
        
        self.log(f"总文件数: {len(all_videos)}, 待处理: {len(videos_to_process)}")
        if not videos_to_process:
            self.on_progress(len(all_videos), len(all_videos))
            return

        completed = 0
        total = len(videos_to_process)
        self.on_progress(0, total)

        max_workers = SettingsManager.get_setting(self.settings, "processing.max_workers", 4)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_video = {executor.submit(self._process_single_video, v): v for v in videos_to_process}
            for future in as_completed(future_to_video):
                res = future.result()
                if res:
                    self.db.upsert_video(res)
                    # 同步更新 L1 缓存
                    with self._cache_lock:
                        self._memory_cache[res['path']] = res
                    
                    # V5.0: 分析完成后自动生成 XMP (Silent Sync)
                    self.sync_metadata_to_xmp([res['path']])
                completed += 1
                self.on_progress(completed, total)
        
        # 同步 L2 (DB) 与 L3 (JSON)
        self._save_l3_backup()
        self.file_manager.save_results_to_csv(self.get_all_videos())
        self.log("分析完成。")

    def _process_single_video(self, video_path: str) -> Optional[Dict]:
        try:
            filename = os.path.basename(video_path)
            self.log(f"正在处理: {filename}")
            
            file_hash = self.processor.get_file_hash(video_path)
            
            proc_res = self.processor.extract_frames(
                video_path,
                max_frames=SettingsManager.get_setting(self.settings, "processing.max_frames", 10),
                target_size=SettingsManager.get_setting(self.settings, "processing.target_size", 512),
                save_thumbnail=True,
                use_scene_detection=SettingsManager.get_setting(self.settings, "processing.enable_scene_detection", True)
            )
            frames = proc_res.get("frames", [])
            thumbnail_path = proc_res.get("thumbnail")
            phash = proc_res.get("phash", "")
            
            if not frames: return None
            
            model_name = SettingsManager.get_setting(self.settings, "api.model_personalization.video_classification", "gemini-2.0-flash")
            cache_key = f"{file_hash}_{model_name}"
            ai_data = self.ai.analyze_video(frames, cache_key=cache_key)
            
            if not ai_data: return None

            # 标签预处理与分类联动 (V5.0)
            # 优先使用 AIHandler 已经校验和分组好的数据
            processed_tags = ai_data.get("tags", [])
            tag_groups = ai_data.get("tag_groups", {})
            raw_category = ai_data.get("category", "Unknown")
            
            # 使用 TagProcessor 进行同义词清理和分类建议 (不再次调用智能分组，因为 AIHandler 已经分好组了)
            processed_tags, suggested_category = self.tag_processor.process(processed_tags, raw_category)
            
            # 同步更新 tag_groups (V5.0 修复: 确保分组内的标签也是清洗过的)
            synced_tag_groups = {}
            for dim_id, group_tags in tag_groups.items():
                clean_group, _ = self.tag_processor.process(group_tags, suggested_category)
                synced_tag_groups[dim_id] = clean_group
            tag_groups = synced_tag_groups
            
            # 更新标签频次 (V5.0)
            for tag in processed_tags:
                self.db.increment_tag_usage(tag)

            transcription = ""
            if SettingsManager.get_setting(self.settings, "processing.enable_audio_transcription"):
                api_key = SettingsManager.get_setting(self.settings, "api.key", "")
                base_url = SettingsManager.get_setting(self.settings, "api.base_url", "")
                transcription = AudioTranscriber.transcribe(video_path, api_key, base_url)

            metadata_injected = False
            if SettingsManager.get_setting(self.settings, "processing.enable_metadata_injection"):
                metadata_injected = MetadataInjector.inject_tags(video_path, ai_data.get("tags", []), ai_data.get("summary", ""))

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
                "emotion": ai_data.get("emotion"),
                "face_clusters": ai_data.get("characters", []),
                "vector_id": ai_data.get("vector_id"),
                "tag_groups": tag_groups,
            }
        except Exception as e:
            self.log(f"处理失败 {video_path}: {e}")
        return None

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
            
            # V4.0: 增强型智能重命名 (V2)
            emotion = item.get("emotion") or ""
            
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

            # {composition} 已下线：替换为空，避免旧模板残留脏值
            new_fn = pattern.replace("{category}", str(category))\
                            .replace("{tags}", str(tags_str))\
                            .replace("{summary}", str(summary_safe))\
                            .replace("{emotion}", str(emotion))\
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
            "Name\tDescription\tKeywords\tCategory\tEmotion\tSummary"
        ]
        
        lines.append("Data")
        
        for item in videos:
            name = item.get("filename", "")
            description = item.get("summary", "").replace("\t", " ").replace("\n", " ")
            tags = item.get("tags", [])
            keywords = ", ".join(tags)
            category = item.get("category", "")
            emotion = item.get("emotion", "")
            summary = description  # 重复一遍以防字段映射不同
            
            # 使用制表符分隔（已去掉 Rating / Composition）
            row = f"{name}\t{description}\t{keywords}\t{category}\t{emotion}\t{summary}"
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
        """
        videos = self.db.get_all_videos()
        if selected_paths:
            videos = [v for v in videos if v.get("path") in selected_paths]
            
        # 获取全局导出设置
        global_settings = self.tag_config.get("global_settings", {})
        export_schemes = global_settings.get("export_schemes", {})
        use_hierarchical = export_schemes.get("xmp_hierarchical", True)
        prefix_category = export_schemes.get("xmp_prefix_category", True)

        # 建立标签库以供转换
        tags_detail = self.db.get_tags_detail()
        zh_to_en = {t["tag_name"]: t.get("name_en") for t in tags_detail if t.get("name_en")}
        en_to_zh = {t.get("name_en"): t["tag_name"] for t in tags_detail if t.get("name_en")}
        
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
            tags = item.get("tags", [])
            summary = escape(item.get("summary", ""))
            category = item.get("category", "")
            
            c1_mood = ""
            h_tags = []
            
            # 处理分类前缀
            if prefix_category and category:
                h_tags.append(f"分类|{category}")

            regions_rdf = ""
            active_person_tags = []

            for t in tags:
                # 翻译标签 (V6.0)
                t_display = translate_tag(t)
                
                if t in person_tags:
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
