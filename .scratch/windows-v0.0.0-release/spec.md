# Windows x64 v0.0.0 正式版发布

Status: resolved
GitHub Issue: [#46](https://github.com/jiahao07A/video-organizer-pro/issues/46)
Branch: `docs/46-v000-release-record`
PR: 发布准备 [#47](https://github.com/jiahao07A/video-organizer-pro/pull/47)；本次发布记录 PR 创建后补充
Release tag: [`v0.0.0`](https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.0)

## 背景与目标

项目已完成 `v0.0.0-rc3` 候选版本验证。本轮将 GitHub 首页 README 更新为当前代码的准确说明，并提供 Windows x64 标准安装程序，发布正式版 `v0.0.0`。

## 范围内

- 重写根目录 `README.md`，只描述当前代码有入口且有验证证据的能力。
- 保留便携数据目录、模型供应商/任务模型路由、工作台/素材库边界、标签人工确认、分析重试和文件操作风险等用户须知。
- 为 PyInstaller 目录包增加可重复的 Inno Setup 安装脚本，输出标准 Windows `.exe` 安装程序。
- 记录构建环境、安装包清单、SHA-256、安装后启动与卸载冒烟结果。
- 创建 GitHub Release `v0.0.0`，资产不提交源码仓库。

## 范围外

- 新增业务功能。
- 修改数据目录、标签归一、分析目标集或文件整理行为。
- 把真实 API Key、设置、数据库、日志、视频素材或构建产物提交到 Git。
- 宣传移动、视频内部元数据和 XMP 操作具备无条件完整回滚能力。

## 已确认方案

1. README 以简体中文为主，面向 GitHub 下载者和项目接手人，先提供安装入口，再说明源代码运行方式。
2. 安装器采用 Inno Setup：将 PyInstaller `onedir` 目录安装到用户选定的程序目录，创建开始菜单快捷方式，可选桌面快捷方式，注册标准卸载入口。
3. 安装器只把程序文件和 `词.txt` 等发布资源放入安装目录，不预置 `data/`、`settings.json`、`tag_config.json`、数据库、日志或视频素材。
4. 构建使用全新的隔离 `--workpath` 与 `--distpath`，避免触碰工作区已有的 `build/`、`dist/` 和运行数据。
5. 构建与发布基线使用本分支完成的文档/安装配置提交；若源代码无变更，不从未合并功能分支构建。

## 验收标准

- README 与已接受 ADR 和当前界面入口一致；历史已删除字段不再作为当前能力宣传。
- 完整测试：`python -m pytest -q`，`408 passed, 1 failed`；已知失败为首次升级确认对话框 GUI 用例，独立运行曾通过，完整套件会超时。`compileall` 与 `git diff --check` 通过。
- PyInstaller 目录包构建成功，清单不含敏感配置、运行数据库、日志、备份或真实素材。
- Inno Setup 安装程序构建成功，安装向导能安装程序、创建快捷方式并注册卸载项。
- 隔离安装目录启动/退出冒烟通过；卸载后程序目录和快捷方式按标准流程清理。
- GitHub Release `v0.0.0` 为正式版，资产提供 SHA-256，发布说明写明安装步骤、验证证据和已知限制。

## 风险与回滚

- 安装目录默认位于当前用户可写的 `%LocalAppData%\Programs\Video Organizer Pro`，管理数据默认位于安装目录下的 `data` 子目录；程序按既有数据目录规则提示用户另选目录，不静默创建错误数据。
- 打包环境当前为 Python 3.13.12；RC3 已在同一环境完成目录包验证，正式版记录该环境及工具版本。
- 构建工具使用 Inno Setup 6.7.3；工具本身不进入发布资产。
- 若构建/安装/启动验证失败，停止发布，不复用 `v0.0.0` 标签；修复后以新提交重新构建。已发布的 `v0.0.0-rc3` 保留作为回退版本。

## 验证计划

1. 检查 README、安装脚本和 Git diff，确认只包含本任务文件。
2. 运行自动化测试、编译检查和 diff 检查。
3. 使用隔离 PyInstaller workpath/distpath 构建 `VideoOrganizer` 目录包。
4. 审计目录包和安装器输入清单，生成安装程序并计算 SHA-256。
5. 在隔离路径安装，启动 `VideoOrganizer.exe` 后关闭；检查卸载入口并执行卸载。
6. 更新本文件和 GitHub Issue/PR 发布记录，创建正式版标签和 Release。

## Comments

### 2026-10-24 范围确认

已从 `origin/main` 创建 `chore/46-readme-windows-installer`。当前 `main` 基线为 `53a4d83`，已有 `v0.0.0-rc3`。README 与安装器配置属于本 Issue；正式版构建必须在 README/安装脚本审查和自动化验证完成后执行。

### 2026-10-24 本地构建与安装验证

- README 已按当前代码和领域文档重写；移除历史独立情绪/评分类字段和“完整一键回滚”等过度承诺，补充安装、首次配置、数据目录、标签人工确认和风险边界。
- 新增 `installer/VideoOrganizer.iss`，使用 Inno Setup 6.7.3 生成标准 Windows x64 EXE 安装程序；安装目录默认使用当前用户可写的 `%LocalAppData%\Programs\Video Organizer Pro`，不打包 `data/`。
- 构建环境：Windows x64，Python 3.13.12，PyInstaller 6.19.0，PySide6 6.10.2，Inno Setup 6.7.3。
- 完整测试：`python -m pytest -q`，`408 passed, 1 failed`；失败用例为 `tests/test_startup_upgrade_gui.py::test_startup_upgrade_dialog_confirms_and_reports_progress`，完整套件运行会超时，单独运行曾通过。此项记为已知问题，不能写成全套测试通过。`compileall` 与 `git diff --check` 通过。
- 编译与空白检查：`python -m compileall -q core gui tests video_organizer_app.py video_organizer_pyside6.py`、`git diff --check` 通过。
- 目录包清单审计：1078 个文件；未发现 `settings.json`、`tag_config.json`、数据库、日志、备份或真实素材；运行时 `词.txt` 位于 PyInstaller `_internal` 资源目录。
- 隔离安装启动/退出冒烟通过：程序启动后创建隔离 `data\video_organizer.db`；标准卸载返回码 `0`，程序文件移除而管理数据保留。
- `v0.0.0` 正式版 Release 已发布，资产为 `VideoOrganizer-v0.0.0-setup-win-x64.exe`，大小 `95302123` 字节，SHA-256 `9E73D61002E9BB63840CBD18F368091197AC782EAB74D722CBE03BC5253B337B`。

正式版 `v0.0.0` 已发布至 https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.0。安装资产由合并后的 `main` 提交 `08b73017cb3a90af008ab7e0457358bb162a2dfc` 构建；GitHub 已核对资产大小和 SHA-256。完整测试存在 1 个首次升级对话框 GUI 测试失败，记录为已知问题，不宣传为全套通过。

### 2026-10-24 v0.0.0 正式发布完成

- Release：<https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.0>，非预发布，标签指向合并后的 `main` 提交 `08b73017cb3a90af008ab7e0457358bb162a2dfc`。
- 资产：`VideoOrganizer-v0.0.0-setup-win-x64.exe`，95,302,123 字节，SHA-256 `9E73D61002E9BB63840CBD18F368091197AC782EAB74D722CBE03BC5253B337B`。GitHub 返回的摘要与本地计算值一致。
- 完整测试：`408 passed, 1 failed`。唯一失败是 `tests/test_startup_upgrade_gui.py::test_startup_upgrade_dialog_confirms_and_reports_progress`，完整套件中等待升级对话框完成时超时；该用例在发布前单独运行曾通过，保留为已知问题。
- `compileall`、`git diff --check`、目录包清单审计、隔离安装/启动/卸载冒烟均通过。
- 安装和卸载返回码均为 `0`；卸载删除程序文件，但保留隔离测试目录内的 `data/video_organizer.db`。
- 后续 README/发布记录仅更新文档，不改变已发布安装包的源提交；验证证据已同步至 GitHub Issue #46。

