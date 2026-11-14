# validated 冻结包隔离安装验收

Status: 当前 Windows 11 隔离安装、启动退出、覆盖安装通过
日期：2026-11-14
构建输入：`.release-build/review-cycle-20261114-validated/dist/VideoOrganizer`（含 MetadataInjector 临时文件所有权修复）

## 执行顺序与结果

按要求先检查 Qt DLL，再从 clean 包编译隔离安装器，最后运行真实安装 smoke。编译前确认输入无 `data`、`.video-organizer-data.json` 和安装标记；没有先运行会写 locator 的功能探针。

1. `rtk proxy python -X utf8 tools/verify_windows_qt_bundle.py .release-build/review-cycle-20261114-validated/dist/VideoOrganizer`：退出码 **0**，Qt6Core/Gui/Widgets 均为 `OK`。
2. `rtk proxy python -X utf8 tools/build_installer.py --iscc 'C:/Users/A2134/AppData/Local/Programs/Inno Setup 6/ISCC.exe' --dist .release-build/review-cycle-20261114-validated/dist --output .release-build/review-cycle-20261114-validated/isolated-installer --isolated-smoke`：退出码 **0**。
3. `rtk proxy python -X utf8 tools/smoke_installer_update.py --installer .release-build/review-cycle-20261114-validated/isolated-installer/VideoOrganizer-v0.0.1-rc10-isolated-smoke.exe --output .release-build/review-cycle-20261114-validated/installation-smoke`：退出码 **0**。

隔离安装器：

- 绝对路径：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-validated/isolated-installer/VideoOrganizer-v0.0.1-rc10-isolated-smoke.exe`
- 大小：**122003651 字节**。
- SHA-256：`8a5e1e332dc72938e8dc87bc74cf7d7cc752173e2679411a80cbed483603bb6f`。
- 此为隔离测试资产，使用独立 AppId，不注册生产卸载项或快捷方式，不能作为正式发布安装器。

原始结果：

```json
{"installed_version":"0.0.1-rc10","startup_exit_code":0,"update_session":"completed","custom_data_preserved":true,"record_count":1,"reinstall_preserved_settings":true}
```

## 独立复核

- 两次安装日志均出现 `Installation process succeeded`，均无需重启。
- 真实冻结 EXE 以 `--background --smoke-exit` 启动并正式退出，退出码 **0**。
- 重新读取 SQLite，`videos` 表记录数为 **1**；仅使用合成记录。
- 重新读取 settings，`ui_preferences.update_smoke_marker` 为 `preserve-me`。
- 使用 `UpdateSession.load(update_cache(application))` 重新读取，`phase=completed`。
- `installation-smoke/application/data` 不存在，没有生成新空库。
- 进程检查确认无 `VideoOrganizer` 进程残留。

## 证据路径

- 结果：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-validated/installation-smoke/result.json`
- 首次安装日志：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-validated/installation-smoke/install-1.log`
- 覆盖安装日志：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-validated/installation-smoke/install-2.log`
- 隔离应用和合成自定义管理数据保留于同一 `installation-smoke` 目录，未删除现场。

## 验证边界

当前主机为 Windows 11 家庭版 10.0.26200 x64。没有 Windows 10 实机或 VM，不宣称 Win10 稳定性通过。本轮使用本地安装器和预置更新会话，未验证公网下载、生产卸载或剪辑软件导入。未进行可见窗口交互或桌面自动化。

没有修改产品、工具或测试源码，没有 commit、推送、发布、自启注册表修改或目录删除。冻结 EXE 独占已释放，供后续功能探针使用。
