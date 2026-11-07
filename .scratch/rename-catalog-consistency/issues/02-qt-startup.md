# 安装后 QtCore DLL 启动失败

Status: claimed

## 范围与验收
修复构建环境中的 Poppler ICU DLL 被打入发布包、遮蔽 Windows 系统 ICU 的问题。
不修改业务数据、重命名规则或用户配置；保留当前未提交修改。
验收：包内无 icuuc.dll；独立进程加载包内 QtCore/QtGui/QtWidgets 成功；实际 exe 出现主窗口；重新编译 0.0.1-rc1 安装包并记录校验值。

## 任务清单
- [x] 复现 WinError 127。
- [x] 比对导入导出函数，确认 20 个 ICU 函数缺失。
- [x] 暂时移开错误 DLL 后加载 QtCore 成功。
- [x] 修正打包配置并增加发布检查。
- [x] 重新构建、验证启动与安装包。

## 验证记录（2026-11-07）
- 旧包加载 Qt6Core.dll 稳定返回 WinError 127；移开 Poppler icuuc.dll 后加载成功。
- `python tools/verify_windows_qt_bundle.py .release-build/qt-fix-dist/VideoOrganizer`：Qt6Core、Qt6Gui、Qt6Widgets 全部加载成功，包内无 icuuc.dll。
- 临时副本中使用仅含 Windows 系统目录的 PATH 启动实际 exe，找到主窗口 `Video Organizer Pro (PySide6)`，发送关闭消息后退出码 0。
- 发布目录审计：1084 个文件，无用户数据库、设置、真实视频或日志。
- 安装器保留 `DisableDirPage=no`；新增单文件 InstallDelete 清理旧版 `_internal/icuuc.dll`，防止覆盖升级残留冲突库。
- 最终 Inno Setup 编译成功，安装包大小 104531686 字节；SHA256：C800F563EB739441F5E9D449BDD397EBE426BC690A73E1CC512E7562746A904F。
- `git diff --check` 通过。
- 尚未在用户的安装目录执行覆盖安装；保留此项人工验收，未访问生产数据库。
- 按用户约定，修复和构建配置改动等待用户验证批准后 commit。

## 风险与回滚
仅适用当前 Windows 10/11 Qt 发布目标，使用系统 ICU。保留旧构建目录；失败时恢复 spec。真实视频和生产数据库不参与测试。
