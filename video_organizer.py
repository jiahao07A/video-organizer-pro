# -*- coding: utf-8 -*-
"""
Legacy Entry Point - Redirects to core service layer.
"""
import sys
import os

# Add the current directory to sys.path to ensure core can be imported
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.video_organizer_service import (
    VideoOrganizerService, 
    DatabaseManager, 
    SettingsManager, 
    VideoOrganizer,
    DB_FILE
)

def main(argv=None):
    """只将本次输入中实际发现的视频交给分析和重命名。"""
    import argparse
    parser = argparse.ArgumentParser(description="Unified AI Video Organizer Service (Legacy Wrapper)")
    parser.add_argument("path", nargs="?", help="视频文件或文件夹路径")
    parser.add_argument("--rename", action="store_true", help="分析完成后执行自动重命名")
    parser.add_argument("--dry-run", action="store_true", help="仅预览重命名结果，不实际改名")
    parser.add_argument("--workers", type=int, help="并发线程数")
    parser.add_argument("--undo", action="store_true", help="执行上一次重命名的撤销操作")
    
    args = parser.parse_args(argv)
    if args.workers is not None and not 1 <= args.workers <= 512:
        parser.error("--workers 必须在 1 到 512 之间")
    try:
        if args.undo:
            return 0 if VideoOrganizerService().rollback_last_session() else 1
        input_path = args.path or input("请输入路径: ").strip().strip('"').strip("'")
        if not input_path or not os.path.exists(input_path):
            print("路径不存在", file=sys.stderr)
            return 1
        service = VideoOrganizerService()
        paths = service.file_manager.scan_videos(os.path.abspath(input_path))
        if not paths:
            print("指定路径中未找到视频。", file=sys.stderr)
            return 1
        options = {"max_workers": args.workers} if args.workers is not None else None
        result = service.run_analysis(paths, execution_options=options)
        if result.get("failed") or result.get("cancelled"):
            print(result.get("message") or "分析未全部完成。", file=sys.stderr)
            return 1
        if args.rename or args.dry_run:
            preview = service.batch_rename(dry_run=args.dry_run, selected_paths=paths)
            for item in preview:
                print(f"{item['path']} → {item['new']}")
            for item in service._last_rename_result["failed_items"]:
                print(f"{item['path']}: {item['reason']}", file=sys.stderr)
            if service._last_rename_result["failed_count"]:
                return 1
        return 0
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
