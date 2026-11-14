# 重命名释放媒体预览句柄：独立复审

结论：此限定修复通过，未发现确认缺陷。未修改产品代码、已有测试或用户数据，未提交。

## 任务清单

- [x] 读取 start_rename 新增分支、真实媒体回归及作者修复记录。
- [x] 核对 set_media(None) 实际释放语义和调用时机。
- [x] 独立运行真实 Qt 四状态、模拟/拒绝/busy/无关预览、外部 Windows 锁失败重试。
- [x] 补充 Windows 等价路径、无播放器、未选更名项边界。
- [x] 核对版本源为 0.0.1-rc11。
- [x] 记录范围与验证限制。

## 代码与范围

`gui/views/workstation.py::start_rename` 仅在实际执行、最终确认后且忙碌复查通过时，比较当前预览与本次选中的 rename_paths。比较使用 normcase(abspath)，匹配才调用既有 set_media(None)，随后创建原 RenameWorker。

既有 `MediaPreviewWidget.set_media(None)` 先 stop，清空 `_path`，再调用真实 QMediaPlayer.setSource(QUrl())；此处确实卸载媒体源，不只暂停或停止播放。不依赖 `_player` 一定初始化，无媒体时安全。服务端重命名逻辑、其他入口和自动恢复播放不在本轮修复范围。

拒绝预览、最终确认选择 No、确认期间进入忙碌、模拟运行和未选中的其他预览均不会进入卸载分支。当前 `core/app_version.py:3` 为 `APP_VERSION = "0.0.1-rc11"`，未作更改。

## 独立运行结果

```powershell
rtk proxy python -X utf8 -m pytest tests/test_rename_preview_lock.py tests/test_review_gui_actions.py tests/test_review_file_operations.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning --tb=short
```

**74 passed in 71.95s**，无线程异常告警。含以下实际验证：

- loaded / playing / paused / stopped 四种状态使用真实 QMediaPlayer、OpenCV 合成 MP4、真实 RenameWorker 和 os.rename；目标视频字节、XMP、SQLite 与工作范围正确。
- 模拟、取消预览、拒绝最后确认、确认期间 busy 均保留原媒体源、素材及记录，未创建替代 worker。
- 更名目标与另一个预览素材无关时，真实播放器 source 保留。
- 外部真实 CreateFileW 只共享读写、不共享 DELETE；卸载本程序播放器后仍能报告 WinError32，原视频、XMP、SQLite 整行与范围完整，目标不存在。CloseHandle 后同入口重试成功。

测试实效：没有替换 QMediaPlayer、os.rename 或服务更名功能；只有确认弹窗和目标选择做确定性隔离。四状态成功项断言物理文件、旁车、数据库、范围，不以 worker 未崩溃替代功能成功。外部锁项同时断言明确失败原因与所有状态未变，避免修复把外部占用误报成功。

## 独立新增边界

仅在 `.scratch/rename-preview-lock/test_independent_boundaries.py` 添加，复用真实合成场景：

```powershell
rtk proxy python -X utf8 -m pytest .scratch/rename-preview-lock/test_independent_boundaries.py -q -W error::pytest.PytestUnhandledThreadExceptionWarning --tb=short
```

**3 passed in 6.66s**：

- 已加载的真实播放器保持原 source，仅将预览路径表示改为父目录大小写变化、正斜杠和 `/./` 的 Windows 等价表示；更名成功、真实 source 清空、字节不变。
- `_player is None` 的真实未初始化预览可直接更名，未强制创建播放器。
- 预览仍在加载但对话框返回空更名路径时，不创建 worker、不清 source、不移动文件。

追加探针首次未导入 scratch 目录外的 qt_app fixture 导致三项 setup error；显式导入项目既有 QApplication fixture 后重跑通过。该调整只在 scratch，不涉及产品或测试断言放宽。

## 边界

本轮是当前 Windows 11 主机上 Qt 离屏进程及 Windows 真实共享锁的源码验证，不代表 Windows 10 实机或新安装包验收。用户安装修复包后的原始场景仍由主代理验收。修复仅解除本程序本次目标的预览占用，外部程序占用仍应保留并报告失败，已验证此行为。
