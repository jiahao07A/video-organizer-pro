"""Windows后台媒体命令不创建控制台；不影响安装向导或显式窗口请求。"""
import os
import subprocess
import threading
from contextlib import contextmanager

_import_lock = threading.RLock()


def hidden_process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@contextmanager
def quiet_ffmpeg_probe():
    """PySceneDetect导入时会探测FFmpeg；仅将此类子进程设为无控制台。

    依赖无公开开关。临时包装Popen而不改第三方文件；窗口策略只作用于
    FFmpeg可执行文件，并在导入结束后还原。其他线程的非媒体进程原样传递。
    """
    if os.name != "nt":
        yield
        return
    with _import_lock:
        original = subprocess.Popen
        def popen(args, *positional, **kwargs):
            command = kwargs.get("executable") or (args[0] if isinstance(args, (list, tuple)) and args else "")
            name = os.path.basename(os.fsdecode(command)).lower()
            flags = kwargs.get("creationflags", 0)
            explicit = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
            if name.startswith("ffmpeg") and not flags & explicit:
                kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
            return original(args, *positional, **kwargs)
        subprocess.Popen = popen
        try:
            yield
        finally:
            subprocess.Popen = original
