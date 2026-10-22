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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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

    from core.manual_test_environment import ManualTestEnvironment, wait_for_background_work
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
        if window is not None:
            try:
                if hasattr(window, "service"):
                    window.service.request_cancel_analysis()
                completed = wait_for_background_work(window, timeout=10.0)
                if not completed:
                    print("警告：部分后台线程未在期限内结束，清理报告将记录结果。", file=sys.stderr)
            except Exception as exc:
                print(f"等待后台任务失败: {exc}", file=sys.stderr)
            try:
                media_preview = getattr(
                    getattr(getattr(window, "workstation_page", None), "detail_panel", None),
                    "media_preview",
                    None,
                )
                if media_preview is not None:
                    media_preview.set_media(None)
                    if app is not None:
                        app.processEvents()
                window.close()
                window.deleteLater()
                if app is not None:
                    app.processEvents()
            except Exception:
                pass
        report = environment.close()
        print(f"临时数据已清理: {'是' if report['temp_data_removed'] else '否'}")
        print(f"诊断目录已保留: {environment.diagnostics_path}")
        if report["errors"]:
            print("清理问题: " + "；".join(report["errors"]), file=sys.stderr)
            return_code = max(return_code, 2)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
