from pathlib import Path
from types import SimpleNamespace

import pytest

import build


def test_build_uses_repo_spec_and_fresh_isolated_output(tmp_path, monkeypatch):
    output_root = tmp_path / "fresh-build"
    calls = []
    monkeypatch.setitem(__import__("sys").modules, "PyInstaller", SimpleNamespace(__version__="test"))

    def fake_run(command, *, cwd, check, shell):
        calls.append((command, cwd, check, shell))
        distpath = Path(command[command.index("--distpath") + 1])
        executable = distpath / "VideoOrganizer" / "VideoOrganizer.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"synthetic executable")

    monkeypatch.setattr(build.subprocess, "run", fake_run)

    executable = build.build(output_root=output_root)

    assert executable.read_bytes() == b"synthetic executable"
    command, cwd, check, shell = calls[0]
    assert command[command.index("--workpath") + 1] == str(output_root / "work")
    assert command[command.index("--distpath") + 1] == str(output_root / "dist")
    assert command[3] == str(build.ROOT / "build_app.spec")
    assert cwd == str(build.ROOT)
    assert check is True
    assert shell is False


def test_build_refuses_existing_output_without_touching_files(tmp_path, monkeypatch):
    output_root = tmp_path / "existing-build"
    output_root.mkdir()
    sentinel = output_root / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setitem(__import__("sys").modules, "PyInstaller", SimpleNamespace(__version__="test"))
    run_called = False

    def unexpected_run(*_args, **_kwargs):
        nonlocal run_called
        run_called = True

    monkeypatch.setattr(build.subprocess, "run", unexpected_run)

    with pytest.raises(FileExistsError):
        build.build(output_root=output_root)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert run_called is False


def test_build_rejects_unsupported_python(monkeypatch):
    monkeypatch.setattr(build.sys, "version_info", (3, 14, 0))

    with pytest.raises(RuntimeError, match="不支持使用 Python 3.14"):
        build.check_python_version()
