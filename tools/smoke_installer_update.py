"""仅用隔离安装器验证安装、数据保留、更新成功核对与二次覆盖安装。

不写生产卸载注册项或快捷方式，不删除测试目录；输出证据保留供审查。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.app_version import APP_VERSION
from core.data_directory import DataDirectoryManager
from core.software_update import UpdateSession, update_cache
from core.video_organizer_service import SettingsManager, VideoOrganizerService


def _wait_for_startup(process, cache: Path, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # 快速正常退出也可能早于父进程轮询；先读取已落盘的启动核对结果。
        if UpdateSession.load(cache).phase == "completed":
            return
        if process.poll() is not None:
            raise RuntimeError(f"程序启动中退出：{process.returncode}")
        time.sleep(0.2)
    raise RuntimeError(f"更新核对超时；测试进程 {process.pid} 保留供诊断")


def run(installer: Path, output: Path):
    if os.name != "nt" or "isolated-smoke" not in installer.name:
        raise ValueError("只允许 Windows 隔离冒烟安装器，不能使用生产安装包")
    output.mkdir(parents=True, exist_ok=False)
    installation = output / "application"
    installation.mkdir()
    data = output / "custom-data"
    data.mkdir()
    service = VideoOrganizerService(db_path=str(data / "video_organizer.db"), results_json=str(data / "results.json"))
    service.settings.setdefault("ui_preferences", {})["update_smoke_marker"] = "preserve-me"
    SettingsManager.save_settings(service.settings, service.db)
    with service.db._managed_connection() as connection:
        connection.execute("INSERT INTO videos(path,filename,summary,status) VALUES(?,?,?,?)", (str(output / "synthetic.mp4"), "synthetic.mp4", "隔离记录", "analyzed"))
    backup = service._storage_backup_manager().create_backup()
    DataDirectoryManager(application_directory=str(installation)).write_locator(str(data))
    def install(index):
        subprocess.run([
            str(installer.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/SP-",
            "/LANG=chinesesimplified", f"/DIR={installation.resolve()}", f"/LOG={output.resolve() / f'install-{index}.log'}",
        ], check=True, timeout=180)
    install(1)
    marker = json.loads((installation / ".video-organizer-install.json").read_text())
    assert marker["version"] == APP_VERSION
    session = UpdateSession(APP_VERSION, "0.0.1-rc1", str(installation.resolve()), str(data.resolve()), str(data.resolve() / "video_organizer.db"), backup, {}, phase="launched")
    cache = update_cache(installation)
    session.save(cache)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = subprocess.Popen([str(installation / "VideoOrganizer.exe"), "--background", "--smoke-exit"], cwd=str(installation), startupinfo=startup)
    _wait_for_startup(process, cache)
    process.wait(timeout=45)
    if process.returncode != 0:
        raise RuntimeError(f"应用退出码：{process.returncode}")
    install(2)
    assert json.loads((data / "settings.json").read_text(encoding="utf-8"))["ui_preferences"]["update_smoke_marker"] == "preserve-me"
    with service.db._managed_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
    assert not (installation / "data").exists(), "不得创建新空库"
    result = {"installed_version": APP_VERSION, "startup_exit_code": process.returncode, "update_session": "completed", "custom_data_preserved": True, "record_count": 1, "reinstall_preserved_settings": True}
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.installer, args.output)
