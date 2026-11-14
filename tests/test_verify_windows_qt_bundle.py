import pytest

from tools import verify_windows_qt_bundle


def test_cli_help_is_available(capsys):
    with pytest.raises(SystemExit) as raised:
        verify_windows_qt_bundle.main(["--help"])

    assert raised.value.code == 0
    assert "bundle" in capsys.readouterr().out


def test_cli_reports_missing_bundle(capsys, tmp_path):
    assert verify_windows_qt_bundle.main([str(tmp_path / "missing")]) == 1

    captured = capsys.readouterr()
    assert "Qt 包检查失败" in captured.err
    assert "未找到发布包内部目录" in captured.err
