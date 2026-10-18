"""启动时管理数据检查与一次性旧结果导入。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional

from core.storage_backup import StorageBackupManager
from core.storage_backup import write_json_atomic


logger = logging.getLogger("VideoOrganizer.startup")

CURRENT_DATABASE_VERSION = 1
COMPLETION_METADATA_KEY = "legacy_json_import"
DEFAULT_BATCH_SIZE = 200
MAX_REPORT_ITEMS = 100
UPGRADE_JOURNAL_NAME = ".startup-upgrade.json"


class UpgradeStatus(str, Enum):
    """启动升级协调器对外报告的状态。"""

    NO_MIGRATION = "no_migration"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class DatabaseValidationError(RuntimeError):
    """数据库不存在、损坏、结构不完整或版本不可用。"""


class UnsupportedDatabaseVersion(DatabaseValidationError):
    """数据库版本高于当前程序能理解的版本。"""


class StartupUpgradeError(RuntimeError):
    """服务启动被升级门禁阻止。"""

    def __init__(self, result: "UpgradeResult") -> None:
        self.result = result
        super().__init__(result.error or result.message or "启动升级未完成")


@dataclass(frozen=True)
class DatabaseInspection:
    exists: bool
    version: Optional[int] = None
    has_core_schema: bool = False


@dataclass
class UpgradeResult:
    """一次检查或执行的脱敏结果，可直接交给服务层或 GUI。"""

    status: UpgradeStatus
    database_path: str
    json_path: str
    message: str = ""
    error: str = ""
    backup_path: str = ""
    report: Dict[str, Any] = field(default_factory=dict)

    @property
    def requires_confirmation(self) -> bool:
        return self.status == UpgradeStatus.NEEDS_CONFIRMATION

    @property
    def ok(self) -> bool:
        return self.status in {
            UpgradeStatus.NO_MIGRATION,
            UpgradeStatus.COMPLETED,
        }


@dataclass(frozen=True)
class _ImportPlan:
    valid_rows: int
    total_rows: int
    invalid_rows: List[Dict[str, Any]]
    duplicate_rows: List[Dict[str, Any]]
    invalid_row_count: int
    duplicate_row_count: int
    source_sha256: str

    @property
    def skipped_rows(self) -> int:
        return self.invalid_row_count + self.duplicate_row_count

    def report(self) -> Dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "imported_rows": 0,
            "skipped_rows": self.skipped_rows,
            "invalid_rows": list(self.invalid_rows),
            "duplicate_rows": list(self.duplicate_rows),
            "invalid_row_count": self.invalid_row_count,
            "duplicate_row_count": self.duplicate_row_count,
            "report_truncated": (
                len(self.invalid_rows) < self.invalid_row_count
                or len(self.duplicate_rows) < self.duplicate_row_count
            ),
            "source_sha256": self.source_sha256,
        }


def _database_path(path: str) -> str:
    return os.path.abspath(os.fspath(path))


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/").lower()


def _read_database_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError) as exc:
        raise DatabaseValidationError("数据库版本标记无效") from exc


def inspect_database_file(
    path: str,
    *,
    verify_integrity: bool = False,
) -> DatabaseInspection:
    """检查数据库版本和核心结构；完整性扫描只在显式验证阶段执行。"""
    database_path = _database_path(path)
    if not os.path.exists(database_path):
        return DatabaseInspection(exists=False)
    if not os.path.isfile(database_path):
        raise DatabaseValidationError(f"数据库路径不是文件: {database_path}")
    if os.path.getsize(database_path) == 0:
        raise DatabaseValidationError(f"数据库文件为空，疑似损坏: {database_path}")

    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(database_path, timeout=30)
        version = _read_database_version(connection)
        if version > CURRENT_DATABASE_VERSION:
            raise UnsupportedDatabaseVersion(
                f"数据库版本 {version} 高于当前程序支持的版本 {CURRENT_DATABASE_VERSION}"
            )
        if verify_integrity:
            check = connection.execute("PRAGMA quick_check").fetchone()
            if not check or check[0] != "ok":
                raise DatabaseValidationError(
                    f"SQLite 完整性检查失败: {database_path}"
                )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required = {"videos", "settings"}
        missing = sorted(required - tables)
        if missing:
            raise DatabaseValidationError(
                "数据库缺少核心表，拒绝按新库处理: " + ", ".join(missing)
            )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(videos)").fetchall()
        }
        if "path" not in columns:
            raise DatabaseValidationError("数据库 videos 表缺少 path 字段")
        return DatabaseInspection(
            exists=True,
            version=version,
            has_core_schema=True,
        )
    except DatabaseValidationError:
        raise
    except sqlite3.DatabaseError as exc:
        raise DatabaseValidationError(f"数据库无法读取，拒绝降级为空库: {exc}") from exc
    except OSError as exc:
        raise DatabaseValidationError(f"数据库文件无法访问: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _remove_database_artifacts(path: str) -> List[str]:
    """删除本次首次导入创建的数据库及 SQLite 辅助文件。"""
    errors: List[str] = []
    for suffix in ("", "-wal", "-shm"):
        candidate = _database_path(path) + suffix
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
            logger.exception("无法清理失败的首次导入数据库: %s", candidate)
    return errors


class StartupUpgradeCoordinator:
    """协调启动检查、确认后的旧 JSON 导入和完成记录。"""

    def __init__(
        self,
        *,
        database_path: str,
        json_path: str,
        backup_dir: str,
        settings_path: str,
        tag_config_path: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        backup_manager_factory: Optional[Callable[..., StorageBackupManager]] = None,
    ) -> None:
        self.database_path = _database_path(database_path)
        self.json_path = os.path.abspath(os.fspath(json_path))
        self.backup_dir = os.path.abspath(os.fspath(backup_dir))
        self.settings_path = os.path.abspath(os.fspath(settings_path))
        self.tag_config_path = os.path.abspath(os.fspath(tag_config_path))
        self.batch_size = max(1, int(batch_size))
        self._backup_manager_factory = backup_manager_factory or StorageBackupManager

    def _backup_manager(self) -> StorageBackupManager:
        return self._backup_manager_factory(
            database_path=self.database_path,
            settings_path=self.settings_path,
            tag_config_path=self.tag_config_path,
            backup_dir=self.backup_dir,
            settings_payload={},
            tag_config_payload={},
        )

    def inspect(self, *, include_import_report: bool = True) -> UpgradeResult:
        """检查数据库版本和核心结构；仅升级预检时读取旧 JSON。"""
        journal = self._read_upgrade_journal()
        if journal is not None:
            if journal.get("status") == "complete":
                self._remove_upgrade_journal()
            else:
                return self._failure(
                    "检测到上次旧 JSON 首次导入未完成，已阻止打开可能的半完成数据库；"
                    "请确认后重试。",
                    report={"incomplete_upgrade": True},
                )
        try:
            database = inspect_database_file(self.database_path)
        except DatabaseValidationError as exc:
            return self._failure(str(exc))

        if database.exists:
            return UpgradeResult(
                status=UpgradeStatus.NO_MIGRATION,
                database_path=self.database_path,
                json_path=self.json_path,
                message="已有有效数据库，启动时不读取或合并旧 JSON。",
                report={"database_version": database.version},
            )
        if not os.path.exists(self.json_path):
            return UpgradeResult(
                status=UpgradeStatus.NO_MIGRATION,
                database_path=self.database_path,
                json_path=self.json_path,
                message="没有旧数据库或旧 JSON，无需迁移。",
            )

        if not include_import_report:
            return UpgradeResult(
                status=UpgradeStatus.NEEDS_CONFIRMATION,
                database_path=self.database_path,
                json_path=self.json_path,
                message="检测到仅有旧 JSON，需要确认后才会首次导入。",
            )

        try:
            plan = self._load_import_plan()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._failure(f"旧 JSON 无法读取或格式无效: {exc}")
        return UpgradeResult(
            status=UpgradeStatus.NEEDS_CONFIRMATION,
            database_path=self.database_path,
            json_path=self.json_path,
            message="检测到仅有旧 JSON，需要确认后才会首次导入。",
            report=plan.report(),
        )

    def execute(
        self,
        *,
        confirmed: bool,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> UpgradeResult:
        """按确认结果执行一次导入；取消和失败都不报告为完成。"""
        inspected = self.inspect()
        if inspected.status == UpgradeStatus.FAILED and inspected.report.get("incomplete_upgrade"):
            if not confirmed:
                return replace(
                    inspected,
                    status=UpgradeStatus.CANCELLED,
                    message="用户取消上次未完成升级的重试，未打开半完成数据库。",
                )
            cleanup_errors = _remove_database_artifacts(self.database_path)
            if cleanup_errors:
                return self._failure(
                    "无法清理上次未完成的数据库: " + " | ".join(cleanup_errors),
                    report={"incomplete_upgrade": True},
                )
            self._remove_upgrade_journal()
            inspected = self.inspect()
        if inspected.status != UpgradeStatus.NEEDS_CONFIRMATION:
            return inspected
        if not confirmed:
            return replace(
                inspected,
                status=UpgradeStatus.CANCELLED,
                message="用户取消旧 JSON 首次导入，未写入主数据。",
            )

        try:
            plan = self._load_import_plan()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._failure(f"旧 JSON 无法读取或格式无效: {exc}")

        # 确认等待期间可能已有另一个进程/窗口创建数据库；再次检查，避免覆盖。
        latest = self.inspect()
        if latest.status != UpgradeStatus.NEEDS_CONFIRMATION:
            return latest
        if latest.report.get("source_sha256") != plan.source_sha256:
            return self._failure("用户确认后旧 JSON 发生变化，已停止导入，请重新确认。")

        source_snapshot = ""
        database_created = False
        backup_path = ""
        manager = None
        try:
            source_snapshot = self._create_source_snapshot(plan.source_sha256)
            self._write_upgrade_journal(
                {
                    "status": "in_progress",
                    "source_sha256": plan.source_sha256,
                    "source_rows": plan.total_rows,
                    "created_at": datetime.now().astimezone().isoformat(),
                }
            )
            os.makedirs(os.path.dirname(self.database_path) or ".", exist_ok=True)
            from core.video_organizer_service import DatabaseManager

            database = DatabaseManager(self.database_path)
            database_created = True
            manager = self._backup_manager()
            backup_path = manager.create_backup()
            manager.validate_backup(backup_path)

            total = plan.valid_rows
            self._notify_progress(on_progress, 0, total)
            imported = 0
            batch: List[Dict[str, Any]] = []
            for row in self._iter_valid_import_rows(source_snapshot):
                batch.append(row)
                if len(batch) < self.batch_size:
                    continue
                database.upsert_videos_batch(batch)
                imported += len(batch)
                self._notify_progress(on_progress, imported, total)
                batch = []
            if batch:
                database.upsert_videos_batch(batch)
                imported += len(batch)
                self._notify_progress(on_progress, imported, total)

            # 只有数据写入、完整性检查和完成记录都成功，才算启用迁移结果。
            inspect_database_file(self.database_path, verify_integrity=True)
            completion = {
                "status": "complete",
                "source_sha256": plan.source_sha256,
                "source_rows": plan.total_rows,
                "imported_rows": imported,
                "completed_at": datetime.now().astimezone().isoformat(),
                "completion_id": uuid.uuid4().hex,
            }
            database.set_storage_metadata(COMPLETION_METADATA_KEY, completion)
            inspect_database_file(self.database_path, verify_integrity=True)
            self._write_upgrade_journal(
                {
                    "status": "complete",
                    "source_sha256": plan.source_sha256,
                    "completed_at": completion["completed_at"],
                }
            )
            self._remove_upgrade_journal()

            report = plan.report()
            report["imported_rows"] = imported
            return UpgradeResult(
                status=UpgradeStatus.COMPLETED,
                database_path=self.database_path,
                json_path=self.json_path,
                message="旧 JSON 已在确认后完成首次导入。",
                backup_path=backup_path,
                report=report,
            )
        except Exception as exc:
            logger.exception("旧 JSON 首次导入失败")
            cleanup_errors = _remove_database_artifacts(self.database_path) if database_created else []
            journal_errors = self._remove_upgrade_journal() if database_created and not cleanup_errors else []
            cleanup_note = "；数据库清理失败: " + " | ".join(cleanup_errors) if cleanup_errors else ""
            journal_note = "；升级日志清理失败: " + " | ".join(journal_errors) if journal_errors else ""
            # 首次导入原本没有旧数据库；保留源 JSON 与失败日志，移除半成品库后可重试。
            message = f"旧 JSON 首次导入失败: {exc}{cleanup_note}{journal_note}"
            result = self._failure(message)
            result.backup_path = backup_path
            return result
        finally:
            if source_snapshot:
                try:
                    os.remove(source_snapshot)
                except OSError:
                    logger.warning("无法清理旧 JSON 导入临时快照: %s", source_snapshot)

    def _load_import_plan(self) -> _ImportPlan:
        source_sha256 = self._sha256_file(self.json_path)
        total_rows = 0
        valid_rows = 0
        invalid_rows: List[Dict[str, Any]] = []
        duplicate_rows: List[Dict[str, Any]] = []
        invalid_row_count = 0
        duplicate_row_count = 0
        with self._path_index() as path_index:
            for row_number, item in enumerate(self._iter_json_array_items(), start=1):
                total_rows += 1
                if not isinstance(item, dict):
                    invalid_row_count += 1
                    if len(invalid_rows) < MAX_REPORT_ITEMS:
                        invalid_rows.append({"row": row_number, "reason": "row_not_object"})
                    continue
                raw_path = item.get("path")
                if not isinstance(raw_path, str) or not raw_path.strip():
                    invalid_row_count += 1
                    if len(invalid_rows) < MAX_REPORT_ITEMS:
                        invalid_rows.append({"row": row_number, "reason": "missing_path"})
                    continue
                path_key = _normalized_path(raw_path.strip())
                try:
                    path_index.execute(
                        "INSERT INTO seen_paths(path) VALUES (?)", (path_key,)
                    )
                except sqlite3.IntegrityError:
                    duplicate_row_count += 1
                    if len(duplicate_rows) < MAX_REPORT_ITEMS:
                        duplicate_rows.append({"row": row_number, "reason": "duplicate_path"})
                    continue
                valid_rows += 1
        return _ImportPlan(
            valid_rows=valid_rows,
            total_rows=total_rows,
            invalid_rows=invalid_rows,
            duplicate_rows=duplicate_rows,
            invalid_row_count=invalid_row_count,
            duplicate_row_count=duplicate_row_count,
            source_sha256=source_sha256,
        )

    @contextmanager
    def _path_index(self) -> Iterator[sqlite3.Connection]:
        """使用磁盘临时 SQLite 索引保存去重键，避免按记录数增长的 Python 集合。"""
        temp_file = tempfile.NamedTemporaryFile(
            prefix="video-organizer-import-", suffix=".sqlite", delete=False
        )
        temp_path = temp_file.name
        temp_file.close()
        connection = sqlite3.connect(temp_path)
        try:
            connection.execute("CREATE TABLE seen_paths (path TEXT PRIMARY KEY)")
            yield connection
        finally:
            connection.close()
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("无法清理导入去重索引: %s", temp_path)

    def _iter_valid_import_rows(self, source_path: str) -> Iterator[Dict[str, Any]]:
        """第二次流式读取，仅产生有效且去重后的行；去重索引落在磁盘。"""
        with self._path_index() as path_index:
            for item in self._iter_json_array_items(source_path):
                if not isinstance(item, dict):
                    continue
                raw_path = item.get("path")
                if not isinstance(raw_path, str) or not raw_path.strip():
                    continue
                path_key = _normalized_path(raw_path.strip())
                try:
                    path_index.execute(
                        "INSERT INTO seen_paths(path) VALUES (?)", (path_key,)
                    )
                except sqlite3.IntegrityError:
                    continue
                yield dict(item)

    def _iter_json_array_items(self, source_path: Optional[str] = None) -> Iterator[Any]:
        """以固定大小缓冲流式解析 JSON 数组，不把整个文件载入内存。"""
        decoder = json.JSONDecoder()
        chunk_size = 64 * 1024
        with open(source_path or self.json_path, "r", encoding="utf-8") as handle:
            buffer = ""
            position = 0
            end_of_file = False

            def refill() -> bool:
                nonlocal buffer, position, end_of_file
                if end_of_file:
                    return False
                remainder = buffer[position:]
                chunk = handle.read(chunk_size)
                if chunk:
                    buffer = remainder + chunk
                    position = 0
                    return True
                buffer = remainder
                position = 0
                end_of_file = True
                return bool(buffer)

            def skip_whitespace() -> bool:
                nonlocal position
                while True:
                    while position < len(buffer) and buffer[position].isspace():
                        position += 1
                    if position < len(buffer):
                        return True
                    if not refill():
                        return False

            if not skip_whitespace() or buffer[position] != "[":
                raise ValueError("顶层必须是数组")
            position += 1

            if not skip_whitespace():
                raise ValueError("JSON 数组缺少结束括号")
            if buffer[position] == "]":
                position += 1
            else:
                while True:
                    while True:
                        try:
                            item, next_position = decoder.raw_decode(buffer, position)
                            break
                        except json.JSONDecodeError as exc:
                            if not refill():
                                raise ValueError(f"JSON 数组元素无效: {exc}") from exc
                    position = next_position
                    yield item
                    if not skip_whitespace():
                        raise ValueError("JSON 数组缺少结束括号")
                    if buffer[position] == ",":
                        position += 1
                        if not skip_whitespace():
                            raise ValueError("JSON 数组缺少元素")
                        continue
                    if buffer[position] == "]":
                        position += 1
                        break
                    raise ValueError("JSON 数组元素之间缺少逗号")

            if skip_whitespace():
                raise ValueError("JSON 数组后存在额外内容")

    @staticmethod
    def _sha256_file(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _create_source_snapshot(self, expected_sha256: str) -> str:
        """确认后创建临时源快照，并只从摘要一致的快照导入。"""
        os.makedirs(self.backup_dir, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            prefix=".legacy-json-", suffix=".snapshot", dir=self.backup_dir, delete=False
        )
        snapshot_path = handle.name
        handle.close()
        try:
            shutil.copy2(self.json_path, snapshot_path)
            actual_sha256 = self._sha256_file(snapshot_path)
            if actual_sha256 != expected_sha256:
                raise ValueError("确认后旧 JSON 发生变化，已停止导入，请重新确认。")
            return snapshot_path
        except Exception:
            try:
                os.remove(snapshot_path)
            except OSError:
                pass
            raise

    def _upgrade_journal_path(self) -> str:
        return os.path.join(self.backup_dir, UPGRADE_JOURNAL_NAME)

    def _read_upgrade_journal(self) -> Optional[Dict[str, Any]]:
        path = self._upgrade_journal_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                journal = json.load(handle)
            if not isinstance(journal, dict):
                return {"status": "invalid"}
            return journal
        except Exception as exc:
            logger.exception("启动升级日志无法读取: %s", path)
            return {"status": "invalid", "error": str(exc)}

    def _write_upgrade_journal(self, journal: Dict[str, Any]) -> None:
        os.makedirs(self.backup_dir, exist_ok=True)
        write_json_atomic(self._upgrade_journal_path(), journal)

    def _remove_upgrade_journal(self) -> List[str]:
        path = self._upgrade_journal_path()
        try:
            if os.path.exists(path):
                os.remove(path)
            return []
        except OSError as exc:
            logger.exception("无法清理启动升级日志: %s", path)
            return [f"{path}: {exc}"]

    def _failure(
        self,
        message: str,
        *,
        report: Optional[Dict[str, Any]] = None,
    ) -> UpgradeResult:
        return UpgradeResult(
            status=UpgradeStatus.FAILED,
            database_path=self.database_path,
            json_path=self.json_path,
            message=message,
            error=message,
            report=report or {},
        )

    @staticmethod
    def _notify_progress(
        callback: Optional[Callable[[int, int], None]],
        current: int,
        total: int,
    ) -> None:
        if callback is None:
            return
        try:
            callback(current, total)
        except Exception:
            logger.exception("启动升级进度回调失败，继续执行迁移。")


__all__ = [
    "COMPLETION_METADATA_KEY",
    "CURRENT_DATABASE_VERSION",
    "UPGRADE_JOURNAL_NAME",
    "DatabaseInspection",
    "DatabaseValidationError",
    "StartupUpgradeCoordinator",
    "StartupUpgradeError",
    "UnsupportedDatabaseVersion",
    "UpgradeResult",
    "UpgradeStatus",
    "inspect_database_file",
]
