# -*- coding: utf-8 -*-
"""从仓库根目录安全构建 Windows 目录包。

发布构建请优先直接调用 PyInstaller 并指定隔离的 ``.release-build`` 输出目录；
此兼容入口不会删除仓库中的 ``build``/``dist``，也不依赖当前工作目录。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = ROOT / ".release-build" / "local-build"


def check_python_version() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) not in {(3, 11), (3, 12), (3, 13)}:
        raise RuntimeError(
            f"不支持使用 Python {major}.{minor} 打包；请使用 Python 3.11、3.12 或 3.13。"
        )


def build(*, output_root: Path) -> Path:
    check_python_version()
    try:
        import PyInstaller
    except ImportError as exc:
        raise RuntimeError("未安装 PyInstaller，请先安装 requirements-dev.txt。") from exc

    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    workpath = output_root / "work"
    distpath = output_root / "dist"
    spec_file = ROOT / "build_app.spec"
    if not spec_file.is_file():
        raise FileNotFoundError(f"找不到打包规格文件: {spec_file}")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec_file),
        "--noconfirm",
        "--workpath",
        str(workpath),
        "--distpath",
        str(distpath),
    ]
    subprocess.run(command, cwd=str(ROOT), check=True, shell=False)
    executable = distpath / "VideoOrganizer" / "VideoOrganizer.exe"
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller 返回成功但未找到输出文件: {executable}")
    return executable


def main() -> int:
    parser = argparse.ArgumentParser(description="安全构建 Video Organizer Windows 目录包")
    parser.add_argument(
        "--output-root",
        type=Path,
        help="本次构建的新输出目录；必须不存在。默认在 .release-build/local-build 下创建唯一目录",
    )
    args = parser.parse_args()
    output_root = args.output_root
    if output_root is None:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        output_root = DEFAULT_OUTPUT_ROOT / run_id
    try:
        executable = build(output_root=output_root)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        return 1
    print(f"打包成功: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
