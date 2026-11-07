"""Windows 发布包检查：独立进程加载包内 Qt DLL，检测同名 ICU 污染。"""
import ctypes
import os
import sys
from pathlib import Path

root = (Path(sys.argv[1]).resolve() / '_internal') if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[1] / '.release-build/dist/VideoOrganizer/_internal'
)
assert not list(root.rglob('icuuc.dll')), '发布包中混入 Windows 系统 ICU 同名库'
handles = [os.add_dll_directory(str(root)), os.add_dll_directory(str(root / 'PySide6'))]
for name in ('Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll'):
    ctypes.WinDLL(str(root / 'PySide6' / name))
    print(f'{name} OK')
