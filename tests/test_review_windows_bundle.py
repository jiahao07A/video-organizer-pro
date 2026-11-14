"""Windows 最低版本与感知哈希打包契约；不替代冻结包实测。"""
import ast
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from core.video_organizer_service import VideoProcessor


ROOT = Path(__file__).resolve().parents[1]


def test_installer_rejects_windows_older_than_qt_supported_build():
    text = (ROOT / "installer" / "VideoOrganizer.iss").read_text(encoding="utf-8")
    setup = text.split("[Setup]", 1)[1].split("[Languages]", 1)[0]
    values = dict(
        line.strip().split("=", 1)
        for line in setup.splitlines()
        if "=" in line and not line.lstrip().startswith((";", "#"))
    )
    assert values["MinVersion"] == "10.0.17763"


def test_build_collects_phash_dct_dependency_instead_of_excluding_it():
    tree = ast.parse((ROOT / "build_app.spec").read_text(encoding="utf-8"))
    hiddenimports = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "all_hiddenimports" for target in node.targets)
    )
    analysis = next(
        node.value for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "Analysis"
    )
    exclusions = ast.literal_eval(next(keyword.value for keyword in analysis.keywords if keyword.arg == "excludes"))
    assert "scipy.fftpack" in hiddenimports
    assert not any(name == "scipy" or name.startswith("scipy.") for name in exclusions)


def test_synthetic_frame_has_real_phash_with_scipy_dct():
    # 非均匀图案确保真实 DCT 工作；同时验证产品入口没有静默返回空串。
    rgb = np.zeros((64, 64, 3), dtype=np.uint8)
    rgb[8:28, 12:44] = (220, 45, 15)
    rgb[36:60, 24:54] = (10, 175, 240)
    expected = str(imagehash.phash(Image.fromarray(rgb)))
    actual = VideoProcessor.calculate_phash(rgb[:, :, ::-1].copy())
    assert len(expected) == 16
    assert int(expected, 16) != 0
    assert actual == expected
