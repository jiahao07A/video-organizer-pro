# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# 自动收集 PySide6, cv2, openai 等库的所有数据、二进制文件和隐藏导入
# 这能极大提高跨机器运行的成功率
def get_all_bundle_data(package_name):
    datas, binaries, hiddenimports = collect_all(package_name)
    return datas, binaries, hiddenimports

# 收集列表
packages = ['PySide6', 'cv2', 'openai', 'imagehash', 'scenedetect', 'qt_material']
all_datas = [
    # 只打包非用户配置资源。严禁收集 .env、settings.json 或 tag_config.json：
    # 它们可能含密钥、个人设置和用户词表；应用默认值由代码提供，写入配置放在 EXE 旁。
    ('词.txt', '.'),
]
all_binaries = []
all_hiddenimports = ['sqlite3', 'PIL.Image', 'PySide6.QtXml']

for pkg in packages:
    d, b, h = get_all_bundle_data(pkg)
    all_datas.extend(d)
    all_binaries.extend(b)
    all_hiddenimports.extend(h)

a = Analysis(
    ['video_organizer_app.py'],
    pathex=[],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VideoOrganizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VideoOrganizer',
)
