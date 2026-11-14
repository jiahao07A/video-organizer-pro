"""检查 Windows 发布包中的 Qt DLL 与 ICU 依赖。"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path


def _default_bundle_root() -> Path:
    return Path(__file__).resolve().parents[1] / ".release-build" / "dist" / "VideoOrganizer"


def verify(bundle: Path) -> None:
    """在独立 DLL 搜索路径中加载发布包所需的 Qt DLL。"""
    root = bundle.resolve() / "_internal"
    if not root.is_dir():
        raise FileNotFoundError(f"未找到发布包内部目录: {root}")
    polluted = list(root.rglob("icuuc.dll"))
    if polluted:
        locations = ", ".join(str(path) for path in polluted)
        raise RuntimeError(f"发布包中混入 Windows 系统 ICU 同名库: {locations}")
    if os.name != "nt":
        raise OSError("Qt DLL 包检查必须在 Windows 上运行")
    handles = []
    try:
        handles.append(os.add_dll_directory(str(root)))
        handles.append(os.add_dll_directory(str(root / "PySide6")))
        for name in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll"):
            ctypes.WinDLL(str(root / "PySide6" / name))
            print(f"{name} OK")
    finally:
        for handle in reversed(handles):
            handle.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bundle",
        nargs="?",
        type=Path,
        help="PyInstaller onedir 应用目录（默认使用 .release-build/dist/VideoOrganizer）",
    )
    args = parser.parse_args(argv)
    bundle = args.bundle or _default_bundle_root()
    try:
        verify(bundle)
    except (AttributeError, FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"Qt 包检查失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
