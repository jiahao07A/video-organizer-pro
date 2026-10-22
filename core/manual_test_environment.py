"""可复用的人工测试会话环境。

该模块只服务于本地人工走查和诊断脚本。它通过显式路径注入创建
``VideoOrganizerService``，因此不会解析或修改项目默认管理数据目录。
"""
from __future__ import annotations

import atexit
import copy
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from core.data_directory import DataPaths
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


DIAGNOSTICS_ENV = "VIDEO_ORGANIZER_ANALYSIS_LOG_DIR"
SESSION_ENV = "VIDEO_ORGANIZER_MANUAL_TEST_SESSION"
DEFAULT_RECORD_COUNT = 8


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


class ManualTestEnvironment:
    """一场人工测试会话的临时数据、合成素材和诊断日志边界。

    ``data_root``、``media_root`` 和其中的配置/结果文件会在会话结束时
    删除。``diagnostics_path`` 位于项目的 ``logs/manual-tests`` 下，专门
    保存应用日志、分析指标和清理报告，不随临时数据删除。
    """

    def __init__(
        self,
        *,
        record_count: int = DEFAULT_RECORD_COUNT,
        diagnostics_root: Optional[str] = None,
        create_media_files: bool = True,
    ) -> None:
        self.record_count = max(0, int(record_count))
        self.create_media_files = bool(create_media_files)
        self.session_id = (
            datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        )
        project_root = Path(__file__).resolve().parents[1]
        base_diagnostics = Path(diagnostics_root or project_root / "logs" / "manual-tests")
        self.diagnostics_path = (base_diagnostics / self.session_id).resolve()
        self.temp_root: Optional[Path] = None
        self.data_root: Optional[Path] = None
        self.media_root: Optional[Path] = None
        self.service: Optional[VideoOrganizerService] = None
        self._old_environment: Dict[str, Optional[str]] = {}
        self._handlers: list[logging.Handler] = []
        self._closed = False
        self._started_at = ""
        self._atexit_registered = False
        self.cleanup_report: Optional[Dict[str, Any]] = None

    @property
    def paths(self) -> DataPaths:
        if self.data_root is None:
            raise RuntimeError("人工测试环境尚未启动")
        return DataPaths.from_root(str(self.data_root))

    @property
    def diagnostics_log(self) -> Path:
        return self.diagnostics_path / "application.log"

    def __enter__(self) -> "ManualTestEnvironment":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def start(self) -> "ManualTestEnvironment":
        if self.temp_root is not None and not self._closed:
            return self
        self._closed = False
        self._started_at = _now()
        self.cleanup_report = None
        self._old_logger_level: Optional[int] = None
        try:
            self.diagnostics_path.mkdir(parents=True, exist_ok=True)
            self._install_logging()
            self.temp_root = Path(tempfile.mkdtemp(prefix="video-organizer-manual-"))
            self.data_root = self.temp_root / "data"
            self.media_root = self.temp_root / "synthetic-media"
            self.data_root.mkdir()
            self.media_root.mkdir()
            if _path_is_within(self.diagnostics_path, self.temp_root):
                raise RuntimeError("诊断目录不能位于临时会话目录内")

            self._set_environment()
            self._write_session_metadata(status="running")
            self._create_service_and_data()
            self._write_session_metadata(status="ready")
            self._atexit_registered = True
            atexit.register(self.close)
            logging.getLogger("VideoOrganizer.manual").info(
                "人工测试环境已启动: session=%s data=%s diagnostics=%s",
                self.session_id,
                self.data_root,
                self.diagnostics_path,
            )
            return self
        except Exception:
            self.close()
            raise

    def _install_logging(self) -> None:
        logger = logging.getLogger("VideoOrganizer")
        if self._old_logger_level is None:
            self._old_logger_level = logger.level
        handler = RotatingFileHandler(
            self.diagnostics_log,
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handler._manual_test_session_id = self.session_id  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        self._handlers.append(handler)

    def _set_environment(self) -> None:
        for name, value in (
            (DIAGNOSTICS_ENV, str(self.diagnostics_path)),
            (SESSION_ENV, self.session_id),
        ):
            self._old_environment[name] = os.environ.get(name)
            os.environ[name] = value

    def _restore_environment(self) -> None:
        for name, old_value in self._old_environment.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value
        self._old_environment.clear()

    def _write_session_metadata(self, *, status: str) -> None:
        payload = {
            "format": "video-organizer-manual-test-session",
            "version": 1,
            "session_id": self.session_id,
            "status": status,
            "started_at": self._started_at,
            "temp_root": str(self.temp_root) if self.temp_root else "",
            "data_root": str(self.data_root) if self.data_root else "",
            "media_root": str(self.media_root) if self.media_root else "",
            "record_count": self.record_count,
        }
        self.diagnostics_path.mkdir(parents=True, exist_ok=True)
        (self.diagnostics_path / "session.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _create_service_and_data(self) -> None:
        assert self.data_root is not None
        assert self.media_root is not None
        paths = self.paths
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings["api"]["key"] = ""
        settings["api"]["base_url"] = ""
        settings["model_providers"] = []
        settings["current_provider_id"] = ""
        settings["ui_preferences"]["remember_work_scope"] = False
        settings["ui_preferences"]["last_work_scope"] = []
        settings_path = Path(paths.settings)
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tag_config = {
            "version": "6.0",
            "global_settings": {
                "language": "zh-CN",
                "output_format": "json",
                "export_schemes": {"filename_pattern": "{category}-{summary}"},
            },
            "tag_groups": [
                {
                    "id": "mood",
                    "name": "氛围",
                    "rules": {"selection_mode": "single", "max_count": 1, "ai_expandable": False},
                    "tags": ["纪实", "平静", "紧张"],
                },
                {
                    "id": "subject",
                    "name": "主体",
                    "rules": {"selection_mode": "multiple", "max_count": 3, "ai_expandable": False},
                    "tags": ["人物", "城市", "自然"],
                },
                {
                    "id": "location",
                    "name": "场景",
                    "rules": {"selection_mode": "multiple", "max_count": 3, "ai_expandable": False},
                    "tags": ["室内", "街道", "公园"],
                },
                {
                    "id": "action",
                    "name": "动作",
                    "rules": {"selection_mode": "multiple", "max_count": 3, "ai_expandable": False},
                    "tags": ["行走", "交谈", "观察"],
                },
                {
                    "id": "custom",
                    "name": "建议",
                    "rules": {"selection_mode": "multiple", "max_count": 5, "ai_expandable": True},
                    "tags": ["合成素材"],
                },
            ],
        }
        Path(paths.tag_config).write_text(
            json.dumps(tag_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.service = VideoOrganizerService(
            settings=settings,
            db_path=paths.database,
            results_json=paths.results_json,
            results_csv=paths.results_csv,
        )
        self.service.manual_test_environment = True
        self._seed_records()

    def _create_synthetic_media_file(self, path: Path) -> bool:
        """Create a tiny valid MP4 for GUI preview checks when FFmpeg is available."""
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=1",
            "-t",
            "1",
            "-an",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            return path.is_file() and path.stat().st_size > 0
        except (OSError, subprocess.CalledProcessError) as exc:
            logging.getLogger("VideoOrganizer.manual").warning(
                "无法生成有效人工测试视频 %s: %s",
                path.name,
                str(exc),
            )
            return False

    def _seed_records(self) -> None:
        assert self.service is not None
        assert self.media_root is not None
        records = []
        for index in range(self.record_count):
            filename = f"synthetic-clip-{index + 1:03d}.mp4"
            media_path = self.media_root / filename
            if self.create_media_files:
                self._create_synthetic_media_file(media_path)
            records.append(
                {
                    "path": str(media_path),
                    "filename": filename,
                    "category": "A-Roll" if index % 2 else "B-Roll",
                    "summary": f"人工测试合成素材 {index + 1}",
                    "tags": ["合成素材", "人物" if index % 2 else "自然"],
                    "tag_groups": {
                        "mood": ["纪实" if index % 3 else "平静"],
                        "subject": ["人物" if index % 2 else "自然"],
                    },
                    "status": ("analyzed", "pending", "failed")[index % 3],
                    "thumbnail": None,
                }
            )
        self.service.db.insert_videos_if_absent(records)
        for group_id, names in {
            "mood": ["纪实", "平静", "紧张"],
            "subject": ["人物", "城市", "自然"],
            "location": ["室内", "街道", "公园"],
            "action": ["行走", "交谈", "观察"],
            "custom": ["合成素材"],
        }.items():
            for name in names:
                self.service.db.add_tag(group_id, name, is_learned=0)

    def close(self) -> Dict[str, Any]:
        """关闭会话并删除临时数据；诊断日志和报告始终保留。"""
        if (
            self._closed
            and self.cleanup_report is not None
            and self.cleanup_report.get("temp_data_removed") is True
        ):
            return self.cleanup_report
        self._closed = True
        errors: list[str] = []
        temp_root = self.temp_root
        data_removed = False
        try:
            if self.service is not None:
                self.service.request_cancel_analysis()
        except Exception as exc:
            errors.append(f"请求取消后台分析失败: {exc}")
        self.service = None

        self._close_logging_handlers()
        if temp_root is not None and temp_root.exists():
            for attempt in range(3):
                try:
                    shutil.rmtree(temp_root)
                    data_removed = True
                    break
                except OSError as exc:
                    if attempt == 2:
                        errors.append(f"临时数据清理失败: {exc}")
                    else:
                        time.sleep(0.1 * (attempt + 1))
        else:
            data_removed = True
        self._restore_environment()

        report = {
            "format": "video-organizer-manual-test-cleanup",
            "version": 1,
            "session_id": self.session_id,
            "started_at": self._started_at,
            "finished_at": _now(),
            "temp_root": str(temp_root) if temp_root else "",
            "temp_data_removed": data_removed,
            "errors": errors,
        }
        self.cleanup_report = report
        self._write_cleanup_report(report)
        return report

    def _close_logging_handlers(self) -> None:
        if self._old_environment:
            self._restore_environment()
        logger = logging.getLogger("VideoOrganizer")
        handlers = list(self._handlers)
        for handler in list(logger.handlers):
            if getattr(handler, "_manual_test_session_id", None) == self.session_id:
                handlers.append(handler)
            elif self.diagnostics_path == Path(
                os.path.abspath(getattr(handler, "baseFilename", ""))
            ).parent:
                handlers.append(handler)
        seen = set()
        for handler in handlers:
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            try:
                logger.removeHandler(handler)
                handler.flush()
                handler.close()
            except Exception:
                pass
        self._handlers.clear()
        if self._old_logger_level is not None:
            logger.setLevel(self._old_logger_level)
            self._old_logger_level = None


    def _register_exit_cleanup(self) -> None:
        if not self._atexit_registered:
            self._atexit_registered = True
            atexit.register(self.close)


    def _write_cleanup_report(self, report: Dict[str, Any]) -> None:
        try:
            self.diagnostics_path.mkdir(parents=True, exist_ok=True)
            (self.diagnostics_path / "cleanup-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._write_session_metadata(
                status="cleaned" if report["temp_data_removed"] else "cleanup_failed"
            )
        except Exception:
            # 诊断报告写失败不应重新抛出并掩盖原始清理结果。
            pass


def wait_for_background_work(window: Any, timeout: float = 10.0) -> bool:
    """等待 GUI 子线程和挂在控件上的 Python 线程结束。

    返回 ``True`` 表示在期限内没有发现仍在运行的线程。此函数只用于
    人工脚本退出阶段，不会强杀线程；分析任务会先由调用方请求协作式取消。
    """
    try:
        from PySide6.QtCore import QThread, QThreadPool
        from PySide6.QtWidgets import QWidget
    except ImportError:
        return True

    deadline = time.monotonic() + max(0.0, float(timeout))
    seen_threads: set[int] = set()
    while time.monotonic() < deadline:
        active = False
        widgets = [window, *window.findChildren(QWidget)]
        for thread in window.findChildren(QThread):
            if id(thread) in seen_threads:
                continue
            seen_threads.add(id(thread))
            if thread.isRunning():
                active = True
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                thread.wait(min(100, remaining))
        for widget in widgets:
            for value in vars(widget).values():
                if isinstance(value, QThread) and id(value) not in seen_threads:
                    seen_threads.add(id(value))
                    if value.isRunning():
                        active = True
                        remaining = max(1, int((deadline - time.monotonic()) * 1000))
                        value.wait(min(100, remaining))
                if isinstance(value, threading.Thread) and value.is_alive():
                    active = True
                if isinstance(value, QThreadPool) and value.activeThreadCount() > 0:
                    active = True
                    remaining = max(1, int((deadline - time.monotonic()) * 1000))
                    value.waitForDone(min(100, remaining))
        if QThreadPool.globalInstance().activeThreadCount() > 0:
            active = True
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            QThreadPool.globalInstance().waitForDone(min(100, remaining))
        if not active:
            return True
        time.sleep(0.01)
    return False


__all__ = [
    "DEFAULT_RECORD_COUNT",
    "DIAGNOSTICS_ENV",
    "ManualTestEnvironment",
    "SESSION_ENV",
    "wait_for_background_work",
]
