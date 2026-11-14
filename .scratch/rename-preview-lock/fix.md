# 工作台重命名预览句柄修复

日期：2026-11-14。状态：限定修复及定向回归完成，产品编辑权释放，待独立复审。

## 修改

仅在 `gui/views/workstation.py::start_rename()` 最后用户确认和忙碌复查通过后、创建 `RenameWorker` 前补充释放：实际执行且详情预览 `_path` 与本次 `rename_paths` 按 `normcase(abspath(...))` 等价时，调用既有 `media_preview.set_media(None)`。该方法清空真实 QMediaPlayer 源，释放 Windows 播放器仍持有的文件句柄。

模拟、不接受预览、拒绝最后确认、确认期间进入忙碌均不卸载。预览其他未涉及文件时保留源。不自动恢复播放，执行完成继续使用既有 `load_data()` 流程。未修改服务文件操作、依赖、其他入口或版本文件；未操作用户素材、配置、安装器或桌面。

## 真实回归

在主代理已有 `tests/test_rename_preview_lock.py` 中补六项测试（原五项保留），全程使用真实 QMediaPlayer、OpenCV 合成 MP4、RenameWorker、临时 SQLite 与真实文件操作，不替换播放器或 `os.rename`。

- 原有 loaded/playing/paused/stopped 四种播放器状态实际重命名全部成功；视频字节、XMP旁车、SQLite目标与工作范围同步一致。
- 模拟只生成预览结果，保留真实 `player.source()`、源文件、数据库与范围。
- 取消预览、拒绝最终确认、确认期间分析锁占用，均不创建新 RenameWorker、不卸载真实媒体源、不修改素材及记录。
- 重命名选定目标时，另一个未选素材的真实预览源保持不变，目标物理更名成功。
- 外部真实 `CreateFileW` 句柄只共享读/写、不共享 DELETE：应用卸载自己的播放器源后仍安全报告 WinError32；源视频字节、XMP、数据库整行和工作范围保持，目标及目标XMP不存在。`CloseHandle` 后同一工作台入口重试成功，视频/XMP/SQLite/范围一致。

先运行已有五项验证：`5 passed in 12.71s`。

最终联合命令：

```powershell
rtk proxy python -X utf8 -m pytest tests/test_rename_preview_lock.py tests/test_review_gui_actions.py tests/test_review_file_operations.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning --tb=short
```

结果：**74 passed in 68.58s (0:01:08)**，线程异常按失败处理，无警告摘要。`git diff --check` 通过。

## 边界

本轮验证在当前 Windows 11 主机的 Qt 离屏进程进行，使用 Windows 实际文件共享锁；不等同 Windows 10 实机或重新构建安装包验收。真实用户原场景还需安装修复包后确认，不提交。
