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

if __name__ == "__main__":
    # Maintain CLI compatibility
    import argparse
    parser = argparse.ArgumentParser(description="Unified AI Video Organizer Service (Legacy Wrapper)")
    parser.add_argument("path", nargs="?", help="视频文件或文件夹路径")
    parser.add_argument("--rename", action="store_true", help="分析完成后执行自动重命名")
    parser.add_argument("--dry-run", action="store_true", help="仅预览重命名结果，不实际改名")
    parser.add_argument("--workers", type=int, help="并发线程数")
    parser.add_argument("--undo", action="store_true", help="执行上一次重命名的撤销操作")
    
    args = parser.parse_args()

    service = VideoOrganizerService()
    
    if args.undo:
        service.rollback_last_session()
        sys.exit(0)

    input_path = args.path or input("请输入路径: ").strip().strip('"').strip("'")
    if not input_path or not os.path.exists(input_path):
        print("路径不存在")
        sys.exit(1)

    if args.workers: service.settings["max_workers"] = args.workers
    
    try:
        service.run_analysis(input_path)
        if args.rename or args.dry_run:
            service.batch_rename(dry_run=args.dry_run)
    except Exception as e:
        print(f"错误: {e}")
