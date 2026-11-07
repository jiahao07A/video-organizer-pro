"""从唯一版本源编译 Inno Setup 安装器；不删除任何构建目录。"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from packaging.version import Version
from core.app_version import APP_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", required=True, type=Path)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--isolated-smoke", action="store_true", help="隔离验证专用：不注册卸载项，不创建快捷方式；禁止公开发布")
    args = parser.parse_args()
    if not args.iscc.is_file():
        parser.error("未找到 Inno Setup 编译器")
    if not (args.dist / "VideoOrganizer" / "VideoOrganizer.exe").is_file():
        parser.error("未找到 PyInstaller onedir 应用")
    if not (Path(__file__).resolve().parent.parent / "installer/languages/ChineseSimplified.isl").is_file():
        parser.error("缺少仓库内固定版本的 Inno Setup 简体中文语言文件")
    args.output.mkdir(parents=True, exist_ok=False)
    numeric = ".".join(map(str, Version(APP_VERSION).release))
    command = [
        str(args.iscc), "/Q", f"/DAppVersion={APP_VERSION}", f"/DAppNumericVersion={numeric}",
        f"/DDistDir={args.dist.resolve()}", f"/O{args.output.resolve()}",
        str(Path(__file__).resolve().parent.parent / "installer" / "VideoOrganizer.iss"),
    ]
    if args.isolated_smoke:
        command.insert(2, "/DIsolatedSmoke=1")
        command.insert(3, f"/FVideoOrganizer-v{APP_VERSION}-isolated-smoke")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
