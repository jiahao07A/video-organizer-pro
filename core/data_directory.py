"""统一管理数据目录、启动定位记录和可恢复目录搬迁。"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from core.storage_backup import write_json_atomic


DATA_DIRECTORY_FORMAT = "video-organizer-data-root"
DATA_DIRECTORY_VERSION = 1
LOCATOR_FILE_NAME = ".video-organizer-data.json"
DATABASE_NAME = "video_organizer.db"
SETTINGS_NAME = "settings.json"
TAG_CONFIG_NAME = "tag_config.json"
RESULTS_JSON_NAME = "video_analysis_results.json"
RESULTS_CSV_NAME = "video_analysis_results.csv"
BACKUP_DIRECTORY_NAME = "backups"
THUMBNAILS_DIRECTORY_NAME = ".thumbnails"


class DataDirectoryError(RuntimeError):
    """管理数据目录无法定位、访问或验证。"""


class DataDirectoryConflictError(DataDirectoryError):
    """目标目录已有管理数据，不能隐式合并或覆盖。"""


class LegacyLayoutDetected(DataDirectoryError):
    """程序旁仍存在未迁移的旧布局。"""

    def __init__(self, inspection: "DataDirectoryInspection") -> None:
        self.inspection = inspection
        super().__init__(
            "检测到程序旁旧管理数据，需要确认后迁移到 data 目录: "
            + ", ".join(inspection.legacy_items)
        )


class DataDirectoryState(str, Enum):
    DEFAULT_NEW = "default_new"
    ACTIVE = "active"
    LEGACY = "legacy"
    INVALID = "invalid"


@dataclass(frozen=True)
class DataPaths:
    """某个管理数据根目录下的全部应用路径。"""

    root: str
    database: str
    settings: str
    tag_config: str
    backups: str
    thumbnails: str
    results_json: str
    results_csv: str

    @classmethod
    def from_root(cls, root: str) -> "DataPaths":
        root_path = os.path.abspath(os.fspath(root))
        return cls(
            root=root_path,
            database=os.path.join(root_path, DATABASE_NAME),
            settings=os.path.join(root_path, SETTINGS_NAME),
            tag_config=os.path.join(root_path, TAG_CONFIG_NAME),
            backups=os.path.join(root_path, BACKUP_DIRECTORY_NAME),
            thumbnails=os.path.join(root_path, THUMBNAILS_DIRECTORY_NAME),
            results_json=os.path.join(root_path, RESULTS_JSON_NAME),
            results_csv=os.path.join(root_path, RESULTS_CSV_NAME),
        )


@dataclass(frozen=True)
class DataDirectoryInspection:
    state: DataDirectoryState
    application_directory: str
    default_root: str
    active_root: str
    locator_path: str
    legacy_items: tuple[str, ...] = ()
    message: str = ""


class DataDirectoryManager:
    """解析固定于程序/可执行文件位置的数据目录。"""

    def __init__(
        self,
        *,
        application_directory: Optional[str] = None,
        locator_path: Optional[str] = None,
    ) -> None:
        self.application_directory = os.path.abspath(
            os.fspath(application_directory)
            if application_directory is not None
            else self._default_application_directory()
        )
        self.default_root = os.path.join(self.application_directory, "data")
        self.locator_path = os.path.abspath(
            os.fspath(locator_path)
            if locator_path is not None
            else os.path.join(self.application_directory, LOCATOR_FILE_NAME)
        )

    @staticmethod
    def _default_application_directory() -> str:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def paths_for(self, root: str) -> DataPaths:
        return DataPaths.from_root(root)

    def default_paths(self) -> DataPaths:
        return self.paths_for(self.default_root)

    def read_locator(self) -> Optional[str]:
        if not os.path.exists(self.locator_path):
            return None
        try:
            with open(self.locator_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            raise DataDirectoryError(f"数据目录定位记录无法读取: {exc}") from exc
        if not isinstance(payload, dict):
            raise DataDirectoryError("数据目录定位记录格式无效")
        if payload.get("format") != DATA_DIRECTORY_FORMAT:
            raise DataDirectoryError("数据目录定位记录格式不受支持")
        if payload.get("version") != DATA_DIRECTORY_VERSION:
            raise DataDirectoryError("数据目录定位记录版本不受支持")
        root = payload.get("data_root")
        if not isinstance(root, str) or not root.strip():
            raise DataDirectoryError("数据目录定位记录缺少有效路径")
        return os.path.abspath(root)

    def write_locator(self, root: str) -> None:
        root_path = os.path.abspath(os.fspath(root))
        payload = {
            "format": DATA_DIRECTORY_FORMAT,
            "version": DATA_DIRECTORY_VERSION,
            "data_root": root_path,
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        write_json_atomic(self.locator_path, payload)

    def inspect(self) -> DataDirectoryInspection:
        selected = self.read_locator()
        if selected is not None:
            try:
                self.ensure_writable(selected, create=False)
                self._validate_existing_database(self.paths_for(selected).database)
            except DataDirectoryError as exc:
                return DataDirectoryInspection(
                    state=DataDirectoryState.INVALID,
                    application_directory=self.application_directory,
                    default_root=self.default_root,
                    active_root=selected,
                    locator_path=self.locator_path,
                    message=str(exc),
                )
            return DataDirectoryInspection(
                state=DataDirectoryState.ACTIVE,
                application_directory=self.application_directory,
                default_root=self.default_root,
                active_root=selected,
                locator_path=self.locator_path,
                message="使用已选择的数据目录。",
            )

        legacy_items = self._legacy_items()
        if legacy_items:
            return DataDirectoryInspection(
                state=DataDirectoryState.LEGACY,
                application_directory=self.application_directory,
                default_root=self.default_root,
                active_root=self.default_root,
                locator_path=self.locator_path,
                legacy_items=tuple(legacy_items),
                message="检测到程序旁旧布局，需要确认迁移。",
            )

        default_paths = self.default_paths()
        if self._has_management_data(default_paths):
            try:
                self.ensure_writable(default_paths.root, create=False)
                self._validate_existing_database(default_paths.database)
            except DataDirectoryError as exc:
                return DataDirectoryInspection(
                    state=DataDirectoryState.INVALID,
                    application_directory=self.application_directory,
                    default_root=self.default_root,
                    active_root=self.default_root,
                    locator_path=self.locator_path,
                    message=str(exc),
                )
            return DataDirectoryInspection(
                state=DataDirectoryState.ACTIVE,
                application_directory=self.application_directory,
                default_root=self.default_root,
                active_root=self.default_root,
                locator_path=self.locator_path,
                message="使用程序旁默认 data 目录。",
            )

        return DataDirectoryInspection(
            state=DataDirectoryState.DEFAULT_NEW,
            application_directory=self.application_directory,
            default_root=self.default_root,
            active_root=self.default_root,
            locator_path=self.locator_path,
            message="将使用程序旁 data 目录。",
        )

    def resolve_active_paths(self, *, allow_legacy: bool = False) -> DataPaths:
        inspection = self.inspect()
        if inspection.state is DataDirectoryState.INVALID:
            raise DataDirectoryError(inspection.message)
        if inspection.state is DataDirectoryState.LEGACY and not allow_legacy:
            raise LegacyLayoutDetected(inspection)
        root = (
            self.application_directory
            if inspection.state is DataDirectoryState.LEGACY
            else inspection.active_root
        )
        self.ensure_writable(root, create=True)
        return self.paths_for(root)

    def ensure_writable(self, root: str, *, create: bool) -> None:
        root_path = os.path.abspath(os.fspath(root))
        if os.path.exists(root_path) and not os.path.isdir(root_path):
            raise DataDirectoryError(f"数据目录路径不是文件夹: {root_path}")
        if not os.path.exists(root_path):
            if not create:
                raise DataDirectoryError(f"已选择的数据目录不存在: {root_path}")
            try:
                os.makedirs(root_path, exist_ok=True)
            except OSError as exc:
                raise DataDirectoryError(f"无法创建数据目录: {exc}") from exc
        probe = None
        try:
            fd, probe = tempfile.mkstemp(prefix=".write-test-", dir=root_path)
            os.close(fd)
            os.remove(probe)
        except OSError as exc:
            if probe and os.path.exists(probe):
                try:
                    os.remove(probe)
                except OSError:
                    pass
            raise DataDirectoryError(f"数据目录不可写: {root_path}: {exc}") from exc

    def has_management_data(self, root: str) -> bool:
        return self._has_management_data(self.paths_for(root))

    def activate_existing(self, root: str) -> DataPaths:
        """打开已有目录，不合并、不覆盖当前或目标数据。"""
        paths = self.paths_for(root)
        if not self._has_management_data(paths):
            raise DataDirectoryError("目标目录没有可打开的管理数据")
        self.ensure_writable(paths.root, create=False)
        self._validate_existing_database(paths.database)
        self.write_locator(paths.root)
        return paths

    def migrate_legacy(self, target_root: Optional[str] = None) -> DataPaths:
        """将程序旁旧布局复制到 data/ 或指定目录，成功验证后才激活。"""
        inspection = self.inspect()
        if inspection.state is not DataDirectoryState.LEGACY:
            raise DataDirectoryError("当前没有待迁移的程序旁旧布局")
        return self._copy_and_activate(
            source_root=self.application_directory,
            target_root=target_root or self.default_root,
        )

    def migrate_active(self, target_root: str) -> DataPaths:
        """复制当前活动数据目录到空目标，验证后写入定位记录。"""
        inspection = self.inspect()
        if inspection.state is DataDirectoryState.LEGACY:
            raise LegacyLayoutDetected(inspection)
        if inspection.state is DataDirectoryState.INVALID:
            raise DataDirectoryError(inspection.message)
        source_root = inspection.active_root
        if not self._has_management_data(self.paths_for(source_root)):
            raise DataDirectoryError("当前数据目录没有可搬迁的管理数据")
        return self._copy_and_activate(source_root=source_root, target_root=target_root)

    def _copy_and_activate(self, *, source_root: str, target_root: str) -> DataPaths:
        source_root = os.path.abspath(os.fspath(source_root))
        target_root = os.path.abspath(os.fspath(target_root))
        if os.path.normcase(source_root) == os.path.normcase(target_root):
            raise DataDirectoryError("数据目录源和目标不能相同")
        if os.path.exists(target_root) and os.listdir(target_root):
            raise DataDirectoryConflictError(
                "目标数据目录已有内容，必须明确选择打开已有目录或清空后重试；未执行合并。"
            )
        parent = os.path.dirname(target_root) or "."
        os.makedirs(parent, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".data-migration-", dir=parent)
        try:
            stage_paths = self.paths_for(stage)
            final_paths = self.paths_for(target_root)
            self._copy_layout(self.paths_for(source_root), stage_paths, final_paths)
            self._validate_copied_layout(stage_paths)
            if os.path.exists(target_root):
                os.rmdir(target_root)
            os.replace(stage, target_root)
            self.write_locator(target_root)
            return final_paths
        except Exception:
            if os.path.exists(stage):
                shutil.rmtree(stage, ignore_errors=True)
            raise

    def open_existing(self, root: str) -> DataPaths:
        """显式打开目标已有目录；不与当前目录合并。"""
        return self.activate_existing(root)


    def _legacy_items(self) -> list[str]:
        candidates = (
            DATABASE_NAME,
            SETTINGS_NAME,
            TAG_CONFIG_NAME,
            BACKUP_DIRECTORY_NAME,
            THUMBNAILS_DIRECTORY_NAME,
            RESULTS_JSON_NAME,
            RESULTS_CSV_NAME,
        )
        return [
            name
            for name in candidates
            if os.path.exists(os.path.join(self.application_directory, name))
        ]

    @staticmethod
    def _has_management_data(paths: DataPaths) -> bool:
        return any(
            os.path.exists(path)
            for path in (
                paths.database,
                paths.settings,
                paths.tag_config,
                paths.backups,
                paths.thumbnails,
                paths.results_json,
                paths.results_csv,
            )
        )

    @staticmethod
    def _validate_existing_database(path: str) -> None:
        if not os.path.exists(path):
            return
        try:
            # 普通目录定位只检查版本和核心结构；完整 quick_check 留给显式
            # 搬迁/复制验证，避免每次启动都扫描大型数据库。
            from core.startup_upgrade import inspect_database_file

            inspect_database_file(path, verify_integrity=False)
        except Exception as exc:
            if isinstance(exc, DataDirectoryError):
                raise
            raise DataDirectoryError(f"数据库无法打开或结构无效: {exc}") from exc

    def _copy_layout(
        self,
        source_paths: DataPaths,
        stage_paths: DataPaths,
        final_paths: DataPaths,
    ) -> None:
        os.makedirs(stage_paths.root, exist_ok=True)
        if os.path.isfile(source_paths.database):
            self._copy_sqlite(source_paths.database, stage_paths.database)
        for source, target in (
            (source_paths.settings, stage_paths.settings),
            (source_paths.tag_config, stage_paths.tag_config),
            (source_paths.results_json, stage_paths.results_json),
            (source_paths.results_csv, stage_paths.results_csv),
        ):
            if os.path.isfile(source):
                shutil.copy2(source, target)
        for source_dir, target_dir in (
            (source_paths.backups, stage_paths.backups),
            (source_paths.thumbnails, stage_paths.thumbnails),
        ):
            if os.path.isdir(source_dir):
                shutil.copytree(source_dir, target_dir)
        self._relocate_thumbnail_references(
            stage_paths.database,
            source_paths,
            final_paths,
        )

    @staticmethod
    def _copy_sqlite(source: str, target: str) -> None:
        source_connection = None
        target_connection = None
        try:
            source_connection = sqlite3.connect(source, timeout=30)
            target_connection = sqlite3.connect(target, timeout=30)
            source_connection.backup(target_connection, pages=-1)
            target_connection.commit()
        except Exception as exc:
            raise DataDirectoryError(f"数据库复制失败: {exc}") from exc
        finally:
            if target_connection is not None:
                target_connection.close()
            if source_connection is not None:
                source_connection.close()

    @staticmethod
    def _relocate_thumbnail_references(
        database_path: str,
        source_paths: DataPaths,
        final_paths: DataPaths,
    ) -> None:
        if not os.path.isfile(database_path):
            return
        source_root = os.path.realpath(source_paths.root)
        source_thumbnails = os.path.realpath(source_paths.thumbnails)
        connection = sqlite3.connect(database_path)
        try:
            rows = connection.execute(
                "SELECT id, thumbnail FROM videos WHERE thumbnail IS NOT NULL"
            ).fetchall()
            for row_id, raw_reference in rows:
                if not raw_reference:
                    continue
                reference = os.fspath(raw_reference)
                candidate = (
                    reference
                    if os.path.isabs(reference)
                    else os.path.join(source_root, reference)
                )
                candidate_real = os.path.realpath(candidate)
                try:
                    inside = os.path.commonpath((source_thumbnails, candidate_real)) == source_thumbnails
                except ValueError:
                    inside = False
                if not inside:
                    continue
                relative = os.path.relpath(candidate_real, source_thumbnails)
                target = os.path.join(".thumbnails", relative).replace(os.sep, "/")
                if os.path.isfile(os.path.join(source_paths.thumbnails, relative)):
                    connection.execute(
                        "UPDATE videos SET thumbnail = ? WHERE id = ?",
                        (target, row_id),
                    )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _validate_copied_layout(paths: DataPaths) -> None:
        DataDirectoryManager._validate_existing_database(paths.database)
        for path, label in (
            (paths.settings, "settings.json"),
            (paths.tag_config, "tag_config.json"),
        ):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception as exc:
                raise DataDirectoryError(f"{label} 无法读取: {exc}") from exc
            if not isinstance(payload, dict):
                raise DataDirectoryError(f"{label} 必须是 JSON 对象")


__all__ = [
    "BACKUP_DIRECTORY_NAME",
    "DATABASE_NAME",
    "DATA_DIRECTORY_FORMAT",
    "DATA_DIRECTORY_VERSION",
    "DataDirectoryConflictError",
    "DataDirectoryError",
    "DataDirectoryInspection",
    "DataDirectoryManager",
    "DataDirectoryState",
    "DataPaths",
    "LegacyLayoutDetected",
    "LOCATOR_FILE_NAME",
    "SETTINGS_NAME",
    "TAG_CONFIG_NAME",
]
