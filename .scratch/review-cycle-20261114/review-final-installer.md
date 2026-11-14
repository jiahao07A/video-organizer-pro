# 最终 clean 构建隔离安装验收

Status: 已通过当前 Windows 11 的隔离安装、启动退出和覆盖安装验收
日期：2026-11-14
构建输入：`.release-build/review-cycle-20261114-verified/dist/VideoOrganizer`

## 执行顺序与结果

按主 Agent 的执行包顺序完成以下三项；没有先运行会写 locator 的感知哈希探针。

### 1. Qt DLL 隔离加载

```powershell
rtk proxy python -X utf8 tools/verify_windows_qt_bundle.py .release-build/review-cycle-20261114-verified/dist/VideoOrganizer
```

退出码 **0**；`Qt6Core.dll OK`、`Qt6Gui.dll OK`、`Qt6Widgets.dll OK`。

### 2. 从 clean 包编译隔离安装器

```powershell
rtk proxy python -X utf8 tools/build_installer.py --iscc 'C:/Users/A2134/AppData/Local/Programs/Inno Setup 6/ISCC.exe' --dist .release-build/review-cycle-20261114-verified/dist --output .release-build/review-cycle-20261114-verified/isolated-installer --isolated-smoke
```

退出码 **0**。输出目录内唯一 `*isolated-smoke*.exe`：

- 路径：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-verified/isolated-installer/VideoOrganizer-v0.0.1-rc10-isolated-smoke.exe`
- 大小：**122,022,318 字节**。
- SHA-256：`3c380cb02ca7af72c9e94877862b7b969cfb8dfe9cae749e2971a54fd203637f`。

该资产为隔离测试专用，不得当作正式发布安装器。工具使用独立 AppId、不注册生产卸载项、不创建生产快捷方式；本轮没有更改自启项或公开发布。

### 3. 真实隔离安装与覆盖安装

```powershell
rtk proxy python -X utf8 tools/smoke_installer_update.py --installer .release-build/review-cycle-20261114-verified/isolated-installer/VideoOrganizer-v0.0.1-rc10-isolated-smoke.exe --output .release-build/review-cycle-20261114-verified/installation-smoke
```

退出码 **0**，原始结果：

```json
{
  "installed_version": "0.0.1-rc10",
  "startup_exit_code": 0,
  "update_session": "completed",
  "custom_data_preserved": true,
  "record_count": 1,
  "reinstall_preserved_settings": true
}
```

- 工具创建了隔离自定义管理目录、1条合成素材记录和真实一致性备份。
- 第一次静默安装成功，真实冻结 EXE 以 `--background --smoke-exit` 启动并正常退出，退出码0。
- 再次独立读取落盘更新会话，`phase=completed`。
- 第二次覆盖安装成功；再次读取配置确认 `ui_preferences.update_smoke_marker=preserve-me`，SQLite 记录数仍为1。
- `installation-smoke/application/data` 不存在，未创建新空库。
- 两份安装日志尾部均为 `Installation process succeeded`、`Need to restart Windows? No`。

## 证据路径

- 结果：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-verified/installation-smoke/result.json`
- 首次安装日志：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-verified/installation-smoke/install-1.log`
- 覆盖安装日志：`D:/Editing/视频素材/脚本/.release-build/review-cycle-20261114-verified/installation-smoke/install-2.log`
- 隔离应用与自定义管理数据均保留在同一 `installation-smoke` 目录，未删除现场。

## 未验证范围

当前主机 Windows 11 家庭版 10.0.26200 x64。没有 Windows 10 实机或虚拟机证据；不能把安装器最低系统门禁、Qt支持声明或本机结果当作Win10稳定性通过。本轮使用本地安装器和预置更新会话，未验证公网 GitHub/国内镜像的真实传输稳定性、实际在线新版本下载、生产卸载注册项或标准卸载流程。未进行可见窗口交互或桌面自动化，没有启动其他冻结实例。

产品代码未修改；没有 commit、推送、发布、再派代理、自启注册表更改或目录删除。
