"""统一管理数据目录、启动定位记录和可恢复目录搬迁。"""
from __future__ import annotations

import hashlib
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
from typing import Optional

from core.storage_backup import (
    STORAGE_IO_LOCK,
    StorageBackupError,
    StorageBackupManager,
    write_json_atomic,
)


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
        config_root = (
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(str(Path.home()), ".config")
        )
        app_key = hashlib.sha256(
            os.path.normcase(self.application_directory).encode("utf-8")
        ).hexdigest()[:16]
        self.fallback_locator_path = os.path.abspath(
            os.path.join(
                config_root,
                "VideoOrganizerPro",
                app_key,
                LOCATOR_FILE_NAME,
            )
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
        candidates = [
            path
            for path in (self.locator_path, self.fallback_locator_path)
            if os.path.exists(path)
        ]
        valid_payloads = []
        for candidate in candidates:
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
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
            valid_payloads.append((payload.get("updated_at", ""), root))
        if not valid_payloads:
            return None
        _, root = max(valid_payloads, key=lambda item: item[0])
        return os.path.abspath(root)

    def write_locator(self, root: str) -> None:
        root_path = os.path.abspath(os.fspath(root))
        payload = {
            "format": DATA_DIRECTORY_FORMAT,
            "version": DATA_DIRECTORY_VERSION,
            "data_root": root_path,
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        try:
            write_json_atomic(self.locator_path, payload)
        except OSError as primary_error:
            if os.path.normcase(self.fallback_locator_path) == os.path.normcase(self.locator_path):
                raise DataDirectoryError(
                    f"无法写入数据目录定位记录: {primary_error}"
                ) from primary_error
            try:
                write_json_atomic(self.fallback_locator_path, payload)
            except OSError as fallback_error:
                raise DataDirectoryError(
                    "程序目录和用户配置目录均无法写入数据目录定位记录: "
                    f"{primary_error}; {fallback_error}"
                ) from fallback_error

    def inspect(self) -> DataDirectoryInspection:
        selected = self.read_locator()
        if selected is not None:
            try:
                self.ensure_writable(selected, create=False)
                self._validate_existing_database(
                    self.paths_for(selected).database,
                    require=True,
                )
                self._validate_existing_data_writable(self.paths_for(selected))
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
                self._validate_existing_database(default_paths.database, require=True)
                self._validate_existing_data_writable(default_paths)
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

    def _validate_existing_data_writable(self, paths: DataPaths) -> None:
        """验证已有管理文件可被当前应用继续写入。"""
        self.ensure_writable(paths.root, create=False)
        for path, label in (
            (paths.database, "数据库"),
            (paths.settings, "设置文件"),
            (paths.tag_config, "标签配置"),
        ):
            if os.path.exists(path) and not os.access(path, os.W_OK):
                raise DataDirectoryError(f"{label}不可写，请选择其他数据目录: {path}")
        for directory, label in (
            (paths.backups, "备份目录"),
            (paths.thumbnails, "缩略图目录"),
        ):
            if os.path.isdir(directory) and not os.access(directory, os.W_OK):
                raise DataDirectoryError(f"{label}不可写，请选择其他数据目录: {directory}")

    def has_openable_management_data(self, root: str) -> bool:
        """目标目录包含可安全打开的有效数据库。"""
        paths = self.paths_for(root)
        if not os.path.isfile(paths.database):
            return False
        try:
            self._validate_existing_database(paths.database, require=True)
            return True
        except DataDirectoryError:
            return False

    def activate_empty(self, root: str) -> DataPaths:
        """选择一个空目录作为活动目录，不隐式覆盖已有管理数据。"""
        paths = self.paths_for(root)
        if os.path.exists(paths.root) and not os.path.isdir(paths.root):
            raise DataDirectoryError(f"数据目录路径不是文件夹: {paths.root}")
        if os.path.isdir(paths.root) and os.listdir(paths.root):
            raise DataDirectoryConflictError("目标目录非空，不能按空目录激活")
        if self._has_management_data(paths):
            raise DataDirectoryConflictError("目标目录已有管理数据，不能按空目录激活")
        self.ensure_writable(paths.root, create=True)
        self.write_locator(paths.root)
        return paths

    def activate_existing(self, root: str) -> DataPaths:
        """打开已有目录，不合并、不覆盖当前或目标数据。"""
        paths = self.paths_for(root)
        if not self._has_management_data(paths) or not os.path.isfile(paths.database):
            raise DataDirectoryError("目标目录没有可打开的管理数据")
        self._validate_existing_data_writable(paths)
        self._validate_existing_database(paths.database, require=True)
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

    def migrate_root(
        self,
        source_root: str,
        target_root: str,
        *,
        source_paths: Optional[DataPaths] = None,
    ) -> DataPaths:
        """搬迁显式指定的数据根目录，验证成功后才激活目标。"""
        source_paths = source_paths or self.paths_for(source_root)
        if os.path.normcase(os.path.realpath(source_paths.root)) != os.path.normcase(
            os.path.realpath(source_root)
        ):
            raise DataDirectoryError("数据目录源路径与管理数据路径不一致")
        if not self._has_management_data(source_paths):
            raise DataDirectoryError("数据目录源没有可搬迁的管理数据")
        return self._copy_and_activate(
            source_root=source_paths.root,
            target_root=target_root,
            source_paths=source_paths,
        )

    def _copy_and_activate(
        self,
        *,
        source_root: str,
        target_root: str,
        source_paths: Optional[DataPaths] = None,
    ) -> DataPaths:
        with STORAGE_IO_LOCK:
            return self._copy_and_activate_locked(
                source_root=source_root,
                target_root=target_root,
                source_paths=source_paths,
            )

    def _copy_and_activate_locked(
        self,
        *,
        source_root: str,
        target_root: str,
        source_paths: Optional[DataPaths] = None,
    ) -> DataPaths:
        source_root = os.path.abspath(os.fspath(source_root))
        target_root = os.path.abspath(os.fspath(target_root))
        source_real = os.path.realpath(source_root)
        target_real = os.path.realpath(target_root)
        source_paths = source_paths or self.paths_for(source_root)
        if os.path.normcase(source_real) == os.path.normcase(target_real):
            raise DataDirectoryError("数据目录源和目标不能相同")
        for managed_dir in (source_paths.backups, source_paths.thumbnails):
            managed_real = os.path.realpath(managed_dir)
            try:
                target_inside_managed = os.path.normcase(
                    os.path.commonpath((managed_real, target_real))
                ) == os.path.normcase(managed_real)
            except ValueError:
                target_inside_managed = False
            if target_inside_managed:
                raise DataDirectoryError("数据目录目标不能位于源备份或缩略图目录内部")
        if os.path.exists(target_root) and not os.path.isdir(target_root):
            raise DataDirectoryError(f"数据目录路径不是文件夹: {target_root}")
        if os.path.isdir(target_root) and os.listdir(target_root):
            raise DataDirectoryConflictError(
                "目标数据目录已有内容，必须明确选择打开已有目录或清空后重试；未执行合并。"
            )
        parent = os.path.dirname(target_root) or "."
        os.makedirs(parent, exist_ok=True)
        target_was_empty_directory = os.path.isdir(target_root)
        stage = tempfile.mkdtemp(prefix=".data-migration-", dir=parent)
        backup_stage = None
        activated = False
        try:
            backup_stage = tempfile.mkdtemp(prefix=".data-backup-", dir=parent)
            stage_paths = self.paths_for(stage)
            final_paths = self.paths_for(target_root)
            migration_backup = self._create_migration_backup(source_paths, backup_stage)
            self._copy_layout(source_paths, stage_paths)
            os.makedirs(stage_paths.backups, exist_ok=True)
            shutil.copytree(
                migration_backup,
                os.path.join(stage_paths.backups, os.path.basename(migration_backup)),
            )
            self._validate_copied_layout(stage_paths)
            if os.path.exists(target_root):
                os.rmdir(target_root)
            os.replace(stage, target_root)
            activated = True
            self.write_locator(target_root)
            return final_paths
        except Exception:
            if activated and os.path.isdir(target_root):
                shutil.rmtree(target_root, ignore_errors=True)
                if target_was_empty_directory:
                    os.makedirs(target_root, exist_ok=True)
            if os.path.exists(stage):
                shutil.rmtree(stage, ignore_errors=True)
            raise
        finally:
            if os.path.exists(backup_stage):
                shutil.rmtree(backup_stage, ignore_errors=True)

    def _create_migration_backup(
        self,
        source_paths: DataPaths,
        backup_dir: str,
    ) -> str:
        """在复制前创建源数据库与配置的一致性快照。"""
        if not os.path.isfile(source_paths.database):
            # 首次 JSON 导入前没有 SQLite 快照可用；将旧布局中的配置、结果
            # 与缩略图复制到独立快照，并校验清单后再继续迁移。
            snapshot_path = os.path.join(backup_dir, "legacy-source-snapshot")
            try:
                os.makedirs(snapshot_path, exist_ok=False)
                for source, name in (
                    (source_paths.settings, SETTINGS_NAME),
                    (source_paths.tag_config, TAG_CONFIG_NAME),
                    (source_paths.results_json, RESULTS_JSON_NAME),
                    (source_paths.results_csv, RESULTS_CSV_NAME),
                ):
                    if os.path.isfile(source):
                        shutil.copy2(source, os.path.join(snapshot_path, name))
                if os.path.isdir(source_paths.thumbnails):
                    shutil.copytree(
                        source_paths.thumbnails,
                        os.path.join(snapshot_path, THUMBNAILS_DIRECTORY_NAME),
                    )
                files = []
                for directory, _, filenames in os.walk(snapshot_path):
                    for filename in filenames:
                        path = os.path.join(directory, filename)
                        relative = os.path.relpath(path, snapshot_path).replace(
                            os.sep, "/"
                        )
                        digest = hashlib.sha256()
                        with open(path, "rb") as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                digest.update(chunk)
                        files.append(
                            {
                                "path": relative,
                                "size": os.path.getsize(path),
                                "sha256": digest.hexdigest(),
                            }
                        )
                manifest = {
                    "format": "video-organizer-migration-source-snapshot",
                    "version": 1,
                    "status": "complete",
                    "files": files,
                }
                write_json_atomic(os.path.join(snapshot_path, "manifest.json"), manifest)
                for item in files:
                    path = os.path.join(snapshot_path, *item["path"].split("/"))
                    digest = hashlib.sha256()
                    with open(path, "rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if (
                        os.path.getsize(path) != item["size"]
                        or digest.hexdigest() != item["sha256"]
                    ):
                        raise DataDirectoryError(
                            f"旧布局快照校验失败: {item['path']}"
                        )
                return snapshot_path
            except Exception as exc:
                if isinstance(exc, DataDirectoryError):
                    raise
                raise DataDirectoryError(f"迁移前备份失败: {exc}") from exc
        try:
            manager = StorageBackupManager(
                database_path=source_paths.database,
                settings_path=source_paths.settings,
                tag_config_path=source_paths.tag_config,
                backup_dir=backup_dir,
                settings_payload={},
                tag_config_payload={},
            )
            backup_path = manager.create_backup()
            manager.validate_backup(backup_path)
            return backup_path
        except StorageBackupError as exc:
            raise DataDirectoryError(f"迁移前备份失败: {exc}") from exc

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
    def _validate_existing_database(
        path: str,
        *,
        verify_integrity: bool = False,
        require: bool = False,
    ) -> None:
        if not os.path.exists(path):
            if require:
                raise DataDirectoryError(f"数据库不存在: {path}")
            return
        if not os.path.isfile(path):
            raise DataDirectoryError(f"数据库路径不是文件: {path}")
        try:
            # 普通目录定位只检查版本和核心结构；完整 quick_check 留给显式
            # 搬迁/复制验证，避免每次启动都扫描大型数据库。
            from core.startup_upgrade import inspect_database_file

            inspect_database_file(path, verify_integrity=verify_integrity)
        except Exception as exc:
            if isinstance(exc, DataDirectoryError):
                raise
            raise DataDirectoryError(f"数据库无法打开或结构无效: {exc}") from exc

    def _copy_layout(
        self,
        source_paths: DataPaths,
        stage_paths: DataPaths,
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
                    inside = os.path.normcase(
                        os.path.commonpath((source_thumbnails, candidate_real))
                    ) == os.path.normcase(source_thumbnails)
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
    def _validate_thumbnail_references(
        paths: DataPaths,
    ) -> None:
        if not os.path.isfile(paths.database):
            return
        target_thumbnails = os.path.realpath(paths.thumbnails)
        connection = sqlite3.connect(paths.database)
        try:
            rows = connection.execute(
                "SELECT id, thumbnail FROM videos WHERE thumbnail IS NOT NULL"
            ).fetchall()
        except sqlite3.Error as exc:
            connection.close()
            raise DataDirectoryError(f"缩略图引用无法读取: {exc}") from exc
        try:
            for row_id, raw_reference in rows:
                if not raw_reference:
                    continue
                reference = os.fspath(raw_reference)
                candidate = (
                    reference
                    if os.path.isabs(reference)
                    else os.path.join(paths.root, reference)
                )
                candidate_real = os.path.realpath(candidate)
                try:
                    inside_target_thumbnails = os.path.normcase(
                        os.path.commonpath((target_thumbnails, candidate_real))
                    ) == os.path.normcase(target_thumbnails)
                except ValueError:
                    inside_target_thumbnails = False
                if os.path.isabs(reference) or not inside_target_thumbnails:
                    raise DataDirectoryError(
                        "缩略图引用未使用目标数据目录内的相对路径 "
                        f"（视频 {row_id}）: {reference}"
                    )
                if not os.path.isfile(candidate):
                    raise DataDirectoryError(
                        f"缩略图引用无效（视频 {row_id}）: {reference}"
                    )
        finally:
            connection.close()

    @staticmethod
    def _validate_copied_layout(
        paths: DataPaths,
    ) -> None:
        DataDirectoryManager._validate_existing_database(
            paths.database,
            verify_integrity=True,
        )
        DataDirectoryManager._validate_thumbnail_references(paths)
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
