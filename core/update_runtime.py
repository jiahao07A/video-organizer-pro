"""Windows 运行互斥量与安装前数据保护。"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from core.app_version import APP_MUTEX, APP_VERSION
from core.data_directory import DataDirectoryManager, DataDirectoryState
from core.software_update import (
    UpdateError, UpdateSession, application_directory, require_space,
    update_cache, verify_installer, version,
)

_mutex_handle = None


def hold_application_mutex():
    """互斥量存活至进程退出；允许多开，但安装器须等全部实例退出。"""
    global _mutex_handle
    if os.name == "nt" and _mutex_handle is None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenMutexW.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_wchar_p]
        kernel.OpenMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        # Inno Setup may start the application from its post-install action
        # before releasing its own mutex. Wait briefly for that normal race;
        # a long-held mutex still indicates a genuinely active installer.
        installing = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            installing = kernel.OpenMutexW(0x100000, False, "VideoOrganizerPro.Installing")
            if not installing:
                break
            kernel.CloseHandle(installing)
            installing = None
            time.sleep(0.1)
        if installing:
            kernel.CloseHandle(installing)
            raise UpdateError("安装器正在运行，请完成或取消安装后再启动应用")
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        _mutex_handle = kernel.CreateMutexW(None, False, APP_MUTEX)
        if not _mutex_handle:
            raise UpdateError("无法建立运行保护，请重试启动")


def is_installed() -> bool:
    marker = application_directory() / ".video-organizer-install.json"
    if not getattr(sys, "frozen", False):
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8-sig"))["app_id"] == "VideoOrganizerPro"
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare_install(service, release, installer: Path, cache: Path):
    ensure_no_other_instances()
    verify_installer(installer, release)
    if version(release.version) <= version(APP_VERSION):
        raise UpdateError("只允许安装比当前版本更新的版本")
    with service.db._managed_connection() as connection:
        database_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if not release.database_min <= database_version <= release.database_max:
        raise UpdateError("新版不支持当前数据格式，请先查看升级说明")
    root = Path(service._data_paths.root)
    db_path = Path(service.db.db_path)
    require_space(application_directory(), release.size * 4 + 64 * 1024**2)
    require_space(root, db_path.stat().st_size * 2 + 16 * 1024**2)
    manager = service._storage_backup_manager()
    backup = manager.create_backup()
    manager.validate_backup(backup)
    directory_manager = DataDirectoryManager()
    locators = {}
    for path in (directory_manager.locator_path, directory_manager.fallback_locator_path):
        if Path(path).is_file():
            # 只存路径定位数据，不包含 API 设置。
            locators[path] = json.loads(Path(path).read_text(encoding="utf-8"))
    session = UpdateSession(
        release.version, APP_VERSION, str(application_directory()), str(root),
        str(db_path), str(backup), locators,
    )
    session.save(cache)
    return session


def ensure_no_other_instances():
    if not getattr(sys, "frozen", False):
        return
    executable = os.path.normcase(os.path.realpath(sys.executable))
    for process in psutil.process_iter(["pid", "exe", "name"]):
        if process.pid == os.getpid():
            continue
        try:
            other = process.info.get("exe")
            if other and os.path.normcase(os.path.realpath(other)) == executable:
                raise UpdateError("检测到其他应用实例，请先正常退出其他实例后再安装")
            if not other and process.info.get("name", "").lower() == Path(sys.executable).name.lower():
                raise UpdateError("无法确认另一个同名进程的状态，请先关闭其他实例")
        except psutil.NoSuchProcess:
            continue


def launch_installer(installer: Path, session: UpdateSession, cache: Path):
    """先记录意图；即使启动瞬间断电，也不能误报安装成功。"""
    ensure_no_other_instances()
    session.phase = "launched"
    session.save(cache)
    try:
        return subprocess.Popen([
            str(installer), f"/DIR={session.application_directory}",
            "/UPDATE=1", "/NORESTART", "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS",
        ], cwd=str(installer.parent), close_fds=True)
    except OSError as exc:
        session.phase = "incomplete"
        session.message = "无法启动安装器，原程序和备份均已保留。"
        session.save(cache)
        raise UpdateError(session.message) from exc


def pending_startup_session(cache=None):
    """必须在常规数据初始化前调用，防止更新后静默创建空库。"""
    cache = cache or update_cache()
    session = UpdateSession.load(cache)
    if session and session.is_pending:
        if Path(session.application_directory).resolve() != application_directory().resolve():
            raise UpdateError("更新记录的安装目录不匹配")
        inspection = DataDirectoryManager().inspect()
        session.validate_data_root(inspection.active_root)
        if inspection.state != DataDirectoryState.ACTIVE:
            raise UpdateError(f"更新前的素材库检查失败，已阻止切换到新空库。\n{inspection.message}\n备份：{session.backup_path}")
    return session
