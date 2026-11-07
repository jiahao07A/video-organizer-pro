# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# 依赖包自身的 PyInstaller hooks 会收集运行所需的动态库和模块。
# 不使用 collect_all：它会把 PySide6 的全部可选模块（包括 Qt3D/QtCharts 等）
# 加入分析，导致构建时间和最终包体不必要地膨胀。
# 只将非用户资源放入包内；.env、settings.json、tag_config.json 不得发布。
all_datas = [
    ('词.txt', '.'),
]
all_hiddenimports = [
    'sqlite3',
    'PIL.Image',
    'PySide6.QtXml',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
]

a = Analysis(
    ['video_organizer_app.py'],
    pathex=[],
    binaries=[],
    datas=all_datas,
    hiddenimports=all_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Optional packages pulled in by the shared Python environment but not used
        # by this application. Excluding them keeps the Windows release practical.
        'torch',
        'tensorflow',
        'transformers',
        'sklearn',
        'pandas',
        'scipy',
        'matplotlib',
        'sympy',
        'sqlalchemy',
        'openpyxl',
        'yt_dlp',
        'sounddevice',
        'soundfile',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Qt 6 使用 Windows 10+ 系统自带的 ICU（不带版本后缀的函数名）。
# 构建机 PATH 中的 Poppler/Conda 可能带同名但 ABI 不兼容的 icuuc.dll；
# 不发布这个系统库，避免它遮蔽 Windows/System32 中的正确实现。
a.binaries = [
    entry for entry in a.binaries
    if entry[0].replace('\\', '/').rsplit('/', 1)[-1].lower() != 'icuuc.dll'
]

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
