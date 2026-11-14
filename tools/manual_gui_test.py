"""启动可复用的临时人工 GUI 测试环境。

示例：
    python tools/manual_gui_test.py --records 8
    python tools/manual_gui_test.py --records 20 --no-media-files
    python tools/manual_gui_test.py --records 8 --offscreen

临时数据库、配置、结果和合成素材会在窗口关闭后删除；诊断日志与
清理报告保存在 ``logs/manual-tests/<session-id>/``。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _drain_background_work(window, app, *, first_timeout=10.0, waiter=None):
    """协作取消并分段等待；超时或检查异常均不能授权销毁活动线程。"""
    from PySide6.QtCore import QThread
    if waiter is None:
        from core.manual_test_environment import wait_for_background_work
        waiter = wait_for_background_work
    service = getattr(window, 'service', None)
    try:
        if service is not None:
            service.request_cancel_analysis()
    except Exception as exc:
        print(f"请求取消后台任务失败，将继续等待：{exc}", file=sys.stderr)
    monitor = getattr(window, 'media_monitor', None) or getattr(service, 'media_monitor', None)
    stop_thread = None
    stop_outcome = {}
    stop_succeeded = monitor is None
    next_stop_attempt = 0.0

    def stop_monitor(outcome):
        try:
            # watchdog.stop 即使不 join，也可能等待事件处理锁，必须离开 GUI 线程。
            monitor.stop(wait=False, shutdown_analysis=True)
        except Exception as exc:
            outcome['error'] = exc
        else:
            outcome['succeeded'] = True

    deadline = time.monotonic() + max(0.0, first_timeout)
    next_notice = deadline
    last_error = None
    idle_checks = 0
    while True:
        if not stop_succeeded and stop_thread is None and time.monotonic() >= next_stop_attempt:
            stop_outcome = {}
            # 持有非守护停止线程，直到其退出；绝不能因主线程结束而遗留清理竞争。
            stop_thread = threading.Thread(target=stop_monitor, args=(stop_outcome,),
                name='manual-test-monitor-stop', daemon=False)
            try:
                stop_thread.start()
            except Exception as exc:
                stop_thread = None
                next_stop_attempt = time.monotonic() + 0.1
                print(f"启动停止监控线程失败，保留数据并重试：{exc}", file=sys.stderr)
        if app is not None:
            app.processEvents()
        if stop_thread is not None and not stop_thread.is_alive():
            stop_thread.join()
            stop_succeeded = stop_outcome.get('succeeded', False)
            if not stop_succeeded:
                error = stop_outcome.get('error', '停止线程未确认成功')
                print(f"请求停止监控失败，保留数据并重试：{error}", file=sys.stderr)
                stop_thread = None
                next_stop_attempt = time.monotonic() + 0.1
        try:
            completed = waiter(window, timeout=0.05)
            if monitor is not None:
                threads = [getattr(monitor, '_thread', None), getattr(monitor, '_observer', None)]
                completed = completed and stop_succeeded and stop_thread is not None and not stop_thread.is_alive()
                completed = completed and not monitor.analysis_running and not any(
                    thread is not None and thread.is_alive() for thread in threads
                )
            last_error = None
        except Exception as exc:
            completed = False
            message = str(exc) or exc.__class__.__name__
            if message != last_error:
                print(f"等待后台任务检查失败，保留窗口和临时数据并继续等待：{message}", file=sys.stderr)
                last_error = message
        # 处理 finished 回调可能提交的后续任务后，再确认一次空闲。
        idle_checks = idle_checks + 1 if completed else 0
        if idle_checks >= 2:
            return
        now = time.monotonic()
        if now >= next_notice:
            print("后台任务尚未确认结束，保留窗口和临时数据，继续等待；不会强制销毁线程。", file=sys.stderr)
            next_notice = now + 30.0
        QThread.msleep(10)


def _close_test_session(environment, window, app, *, first_timeout=10.0, waiter=None):
    """只有确认后台任务全部停止后，才销毁窗口并清理会话数据。"""
    if window is not None:
        _drain_background_work(window, app, first_timeout=first_timeout, waiter=waiter)
        media_preview = getattr(
            getattr(getattr(window, 'workstation_page', None), 'detail_panel', None),
            'media_preview', None,
        )
        if media_preview is not None:
            media_preview.set_media(None)
            if app is not None:
                app.processEvents()
        window.close()
        window.deleteLater()
        if app is not None:
            app.processEvents()
    return environment.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="启动视频素材整理工具的临时人工测试环境")
    parser.add_argument("--records", type=int, default=8, help="预置合成素材记录数，默认 8")
    parser.add_argument(
        "--diagnostics-root",
        default=None,
        help="诊断目录根路径，默认 logs/manual-tests",
    )
    parser.add_argument(
        "--no-media-files",
        action="store_true",
        help="只生成数据库记录，不生成合成媒体占位文件",
    )
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="使用 Qt offscreen 平台；适用于只验证启动和数据，不进行可见操作",
    )
    args = parser.parse_args()
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication, QMessageBox

    from core.manual_test_environment import ManualTestEnvironment
    from gui.main_window import MainWindow

    environment = ManualTestEnvironment(
        record_count=args.records,
        diagnostics_root=args.diagnostics_root,
        create_media_files=not args.no_media_files,
    )
    window = None
    app = None
    try:
        environment.start()
        assert environment.service is not None
        assert environment.media_root is not None
        # 将合成素材放入工作范围，但不保存到设置，避免人工测试影响任何持久数据。
        environment.service.replace_work_scope([str(environment.media_root)], scan=False)

        app = QApplication.instance() or QApplication(sys.argv)
        window = MainWindow(environment.service)
        window.show()
        print(f"临时测试会话已启动: {environment.session_id}")
        print(f"临时数据（退出后删除）: {environment.temp_root}")
        print(f"诊断数据（退出后保留）: {environment.diagnostics_path}")
        print("关闭窗口后会请求取消后台任务、等待线程并清理临时数据。")
        if args.offscreen:
            app.processEvents()
            return_code = 0
        else:
            return_code = app.exec()
    except Exception as exc:
        if app is not None:
            QMessageBox.critical(window, "临时测试环境启动失败", str(exc))
        else:
            print(f"临时测试环境启动失败: {exc}", file=sys.stderr)
        return_code = 1
    finally:
        report = _close_test_session(environment, window, app)
        print(f"临时数据已清理: {'是' if report['temp_data_removed'] else '否'}")
        print(f"诊断目录已保留: {environment.diagnostics_path}")
        if report["errors"]:
            print("清理问题: " + "；".join(report["errors"]), file=sys.stderr)
            return_code = max(return_code, 2)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
