"""一致性管理数据快照与受控恢复。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime
from typing import Any, Dict, Optional


logger = logging.getLogger("VideoOrganizer.storage")

BACKUP_FORMAT = "video-organizer-backup"
BACKUP_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "database.sqlite"
SETTINGS_NAME = "settings.json"
TAG_CONFIG_NAME = "tag_config.json"
RESTORE_JOURNAL_NAME = ".restore-journal.json"

# Settings/tag configuration writes use the same process-local boundary as
# backup capture. This does not claim cross-file atomicity; it prevents a
# backup from copying a file halfway through an in-process atomic replace.
STORAGE_IO_LOCK = threading.RLock()


class StorageBackupError(RuntimeError):
    """管理数据快照无法创建、校验或恢复。"""


class StorageBackupManager:
    """创建和恢复数据库与配套配置的可校验快照。

    快照只包含管理数据，不包含原始视频。SQLite 数据库使用 backup API，
    因此 WAL 中已经提交的内容也会进入快照。配置文件通过同进程锁与
    原子替换配合；跨文件操作通过恢复日志和失败回滚保持可恢复。
    """

    def __init__(
        self,
        *,
        database_path: str,
        settings_path: str,
        tag_config_path: str,
        backup_dir: str,
        database_lock: Optional[threading.RLock] = None,
        io_lock: Optional[threading.RLock] = None,
    ) -> None:
        self.database_path = os.path.abspath(str(database_path))
        self.settings_path = os.path.abspath(str(settings_path))
        self.tag_config_path = os.path.abspath(str(tag_config_path))
        self.backup_dir = os.path.abspath(str(backup_dir))
        self.database_lock = database_lock
        self.io_lock = io_lock or STORAGE_IO_LOCK

    def create_backup(self) -> str:
        """创建完整快照并返回快照目录；失败时不留下可见半成品。"""
        with self.io_lock:
            with self._database_guard():
                return self._create_backup_locked()

    def _create_backup_locked(self) -> str:
        os.makedirs(self.backup_dir, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".backup-staging-", dir=self.backup_dir)
        try:
            database_target = os.path.join(stage, DATABASE_NAME)
            self._snapshot_database(self.database_path, database_target)
            files = {
                "database": {
                    "name": DATABASE_NAME,
                    "present": True,
                    "required": True,
                    "size": os.path.getsize(database_target),
                    "sha256": self._sha256(database_target),
                },
                "settings": self._capture_file(
                    self.settings_path,
                    os.path.join(stage, SETTINGS_NAME),
                    required=False,
                ),
                "tag_config": self._capture_file(
                    self.tag_config_path,
                    os.path.join(stage, TAG_CONFIG_NAME),
                    required=False,
                ),
            }
            manifest = {
                "format": BACKUP_FORMAT,
                "version": BACKUP_VERSION,
                "status": "complete",
                "created_at": datetime.now().astimezone().isoformat(),
                "files": files,
            }
            self._write_json_atomic(os.path.join(stage, MANIFEST_NAME), manifest)
            self._validate_backup_directory(stage)

            final_name = "backup_{}_{}".format(
                datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
                uuid.uuid4().hex[:8],
            )
            final_path = os.path.join(self.backup_dir, final_name)
            os.replace(stage, final_path)
            return final_path
        except Exception as exc:
            shutil.rmtree(stage, ignore_errors=True)
            if isinstance(exc, StorageBackupError):
                raise
            raise StorageBackupError(f"创建管理数据快照失败: {exc}") from exc

    def validate_backup(self, backup_ref: str) -> Dict[str, Any]:
        """校验快照清单、文件摘要和 SQLite 完整性并返回清单。"""
        backup_path = self._resolve_backup_path(backup_ref)
        return self._validate_backup_directory(backup_path)

    def restore(self, backup_ref: str) -> str:
        """恢复快照，返回恢复前自动保护的快照目录。

        所有目标文件先经过暂存和快照校验。应用过程中若发生错误，
        会使用恢复前快照回滚；若进程在中间退出，下一次初始化会依据
        恢复日志继续回滚，而不是把混合版本当作成功结果。
        """
        with self.io_lock:
            with self._database_guard():
                backup_path = self._resolve_backup_path(backup_ref)
                manifest = self._validate_backup_directory(backup_path)
                rollback_path = self._create_backup_locked()
                journal = {
                    "format": BACKUP_FORMAT,
                    "version": BACKUP_VERSION,
                    "status": "applying",
                    "source_backup": backup_path,
                    "rollback_backup": rollback_path,
                }
                journal_path = self._journal_path()
                self._write_json_atomic(journal_path, journal)
                try:
                    self._apply_snapshot_locked(backup_path, manifest)
                    self._validate_live_database()
                    os.remove(journal_path)
                    return rollback_path
                except Exception as exc:
                    try:
                        rollback_manifest = self._validate_backup_directory(rollback_path)
                        self._apply_snapshot_locked(rollback_path, rollback_manifest)
                        self._validate_live_database()
                        os.remove(journal_path)
                    except Exception as rollback_exc:
                        raise StorageBackupError(
                            "恢复失败，且自动回滚未完成；保留恢复日志以便下次启动继续回滚: "
                            f"{rollback_exc}"
                        ) from exc
                    raise StorageBackupError(f"恢复管理数据失败，已回滚当前数据: {exc}") from exc

    def recover_pending_restore(self) -> bool:
        """启动前处理上次中断的恢复；成功回滚返回 True。"""
        journal_path = self._journal_path()
        if not os.path.exists(journal_path):
            return False

        with self.io_lock:
            try:
                with open(journal_path, "r", encoding="utf-8") as handle:
                    journal = json.load(handle)
                if (
                    journal.get("format") != BACKUP_FORMAT
                    or journal.get("version") != BACKUP_VERSION
                    or journal.get("status") != "applying"
                ):
                    raise StorageBackupError("恢复日志版本或状态无法识别")
                rollback_path = journal.get("rollback_backup")
                rollback_manifest = self._validate_backup_directory(rollback_path)
                with self._database_guard():
                    self._apply_snapshot_locked(rollback_path, rollback_manifest)
                    self._validate_live_database()
                os.remove(journal_path)
                logger.warning("已根据恢复日志回滚上次未完成的管理数据恢复。")
                return True
            except StorageBackupError:
                raise
            except Exception as exc:
                raise StorageBackupError(
                    f"无法处理上次未完成的管理数据恢复: {exc}"
                ) from exc

    def list_valid_backups(self) -> list[str]:
        """只列出带完整清单且校验通过的快照目录。"""
        if not os.path.isdir(self.backup_dir):
            return []
        paths: list[str] = []
        for name in sorted(os.listdir(self.backup_dir), reverse=True):
            path = os.path.join(self.backup_dir, name)
            if not os.path.isdir(path) or not name.startswith("backup_"):
                continue
            try:
                self._validate_backup_directory(path)
            except StorageBackupError:
                continue
            paths.append(path)
        return paths

    def _capture_file(self, source: str, target: str, *, required: bool) -> Dict[str, Any]:
        present = os.path.isfile(source)
        entry: Dict[str, Any] = {
            "name": os.path.basename(target),
            "present": present,
            "required": required,
        }
        if not present:
            if required:
                raise StorageBackupError(f"必需的管理数据不存在: {source}")
            return entry
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
        entry["size"] = os.path.getsize(target)
        entry["sha256"] = self._sha256(target)
        return entry

    @staticmethod
    def _snapshot_database(source_path: str, target_path: str) -> None:
        if not os.path.isfile(source_path):
            raise StorageBackupError(f"数据库不存在: {source_path}")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        source = None
        target = None
        try:
            source = sqlite3.connect(source_path, timeout=30)
            target = sqlite3.connect(target_path, timeout=30)
            source.backup(target, pages=-1)
            target.commit()
        except Exception as exc:
            raise StorageBackupError(f"SQLite 一致性快照失败: {exc}") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

    def _validate_backup_directory(self, backup_path: str) -> Dict[str, Any]:
        if not backup_path or not os.path.isdir(backup_path):
            raise StorageBackupError(f"备份目录不存在: {backup_path}")
        manifest_path = os.path.join(backup_path, MANIFEST_NAME)
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except Exception as exc:
            raise StorageBackupError(f"备份清单无法读取: {exc}") from exc

        if not isinstance(manifest, dict):
            raise StorageBackupError("备份清单格式无效")
        if manifest.get("format") != BACKUP_FORMAT:
            raise StorageBackupError("备份格式不受支持")
        if manifest.get("version") != BACKUP_VERSION:
            raise StorageBackupError("备份版本不受支持")
        if manifest.get("status") != "complete":
            raise StorageBackupError("备份尚未完成")

        files = manifest.get("files")
        if not isinstance(files, dict) or "database" not in files:
            raise StorageBackupError("备份缺少数据库清单")
        for key, entry in files.items():
            if not isinstance(entry, dict):
                raise StorageBackupError(f"备份文件清单无效: {key}")
            present = bool(entry.get("present"))
            name = entry.get("name")
            if not isinstance(name, str) or not name or os.path.basename(name) != name:
                raise StorageBackupError(f"备份文件名无效: {key}")
            path = os.path.join(backup_path, name)
            if not present:
                if entry.get("required"):
                    raise StorageBackupError(f"备份缺少必需文件: {key}")
                continue
            if not os.path.isfile(path):
                raise StorageBackupError(f"备份文件不存在: {name}")
            expected_size = entry.get("size")
            expected_hash = entry.get("sha256")
            if expected_size != os.path.getsize(path) or expected_hash != self._sha256(path):
                raise StorageBackupError(f"备份文件校验失败: {name}")

        database_entry = files["database"]
        if database_entry.get("name") != DATABASE_NAME or not database_entry.get("present"):
            raise StorageBackupError("备份数据库文件清单无效")
        self._validate_sqlite(os.path.join(backup_path, DATABASE_NAME))
        return manifest

    def _apply_snapshot_locked(self, backup_path: str, manifest: Dict[str, Any]) -> None:
        files = manifest["files"]
        targets = {
            "database": self.database_path,
            "settings": self.settings_path,
            "tag_config": self.tag_config_path,
        }
        # 数据库和配置都先复制到目标文件同目录的临时文件，再逐个原子替换。
        # 恢复日志保证进程中断后下次启动继续使用回滚快照收敛到一致状态。
        for key in ("database", "settings", "tag_config"):
            entry = files.get(key)
            target = targets[key]
            if not isinstance(entry, dict):
                continue
            if not entry.get("present"):
                if key != "database" and os.path.exists(target):
                    os.remove(target)
                continue
            source = os.path.join(backup_path, entry["name"])
            if key == "database":
                self._restore_database_snapshot(source, target)
                continue
            temp_target = os.path.join(
                os.path.dirname(target),
                f".{os.path.basename(target)}.restore-{uuid.uuid4().hex}.tmp",
            )
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, temp_target)
                os.replace(temp_target, target)
            finally:
                if os.path.exists(temp_target):
                    os.remove(temp_target)

    @staticmethod
    def _restore_database_snapshot(source_path: str, target_path: str) -> None:
        source = None
        target = None
        try:
            source = sqlite3.connect(source_path, timeout=30)
            target = sqlite3.connect(target_path, timeout=30)
            source.backup(target, pages=-1)
            target.commit()
        except Exception as exc:
            raise StorageBackupError(f"SQLite 数据库恢复失败: {exc}") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

    def _validate_live_database(self) -> None:
        self._validate_sqlite(self.database_path)

    @staticmethod
    def _validate_sqlite(path: str) -> None:
        if not os.path.isfile(path):
            raise StorageBackupError(f"SQLite 数据库不存在: {path}")
        connection = None
        try:
            connection = sqlite3.connect(path, timeout=30)
            row = connection.execute("PRAGMA quick_check").fetchone()
            if not row or row[0] != "ok":
                raise StorageBackupError(f"SQLite 完整性检查失败: {path}")
        except StorageBackupError:
            raise
        except Exception as exc:
            raise StorageBackupError(f"SQLite 数据库无法打开: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def _resolve_backup_path(self, backup_ref: str) -> str:
        if not backup_ref:
            raise StorageBackupError("未指定备份目录")
        root = os.path.realpath(self.backup_dir)
        candidate = os.path.realpath(
            backup_ref if os.path.isabs(backup_ref) else os.path.join(root, backup_ref)
        )
        try:
            inside = os.path.commonpath((root, candidate)) == root
        except ValueError:
            inside = False
        if not inside or candidate == root:
            raise StorageBackupError("备份目录必须位于当前备份根目录内")
        return candidate

    def _journal_path(self) -> str:
        os.makedirs(self.backup_dir, exist_ok=True)
        return os.path.join(self.backup_dir, RESTORE_JOURNAL_NAME)

    @staticmethod
    def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent,
                prefix=f".{os.path.basename(path)}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    @staticmethod
    def _sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _database_guard(self):
        return self.database_lock if self.database_lock is not None else nullcontext()
