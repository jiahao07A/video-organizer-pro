# Windows x64 v0.0.0-rc3 发布记录

Status: resolved
GitHub Issue: [#43](https://github.com/jiahao07A/video-organizer-pro/issues/43)
PR: [#44](https://github.com/jiahao07A/video-organizer-pro/pull/44)（代码修复）
Source commit: `830b12bfbd55e40b1bdacfd6a6eb3764171896fb`
Release tag: `v0.0.0-rc3`
Release: https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.0-rc3

## 背景与目标

在 PR #44 修复标签词表导入格式后，从合并后的最新 `main` 构建并发布 Windows x64 下一候选版本。候选包不得从功能分支构建，必须与源提交、标签和发布记录一致。

## 本次结果

- PR #44 已通过审阅并以 Rebase and merge 合入 `main`，合并提交为 `830b12bfbd55e40b1bdacfd6a6eb3764171896fb`。
- Issue #42 已由 PR #44 自动关闭。
- `v0.0.0-rc3` 已创建为 GitHub Pre-release（预发布）。
- 发布资产：`VideoOrganizer-v0.0.0-rc3-win-x64.zip`。

## 构建与验证

- 构建环境：Windows 11 x64，Python 3.13.12，PyInstaller 6.19.0，PySide6 6.10.2。
- 构建命令使用 `build_app.spec`，并指定全新隔离 `--workpath` 与 `--distpath`；未运行会清理默认 `build/`、`dist/` 的 `build.py`。
- 自动化验证：`python -m pytest -q -k "not test_startup_upgrade_dialog_confirms_and_reports_progress"` 为 `408 passed, 1 deselected`；`python -m compileall -q core gui tests video_organizer_app.py video_organizer_pyside6.py` 通过；`git diff --check` 通过。
- 隔离启动/退出冒烟通过；程序在隔离副本中启动，创建隔离 `data` 目录后正常退出。
- ZIP 清单审计通过：未发现 `.env`、个人设置、标签配置、数据库、日志、备份、缩略图缓存或真实视频素材。
- 资产大小：134785661 字节。
- SHA-256：`7DEF923AE0786186B846D475793B466A6B9B87528BB2C1EF3F910F56E916C915`。
- 原工作区既有 `build/`、`dist/` 未被构建过程清理或覆盖。

## 未验证项与风险

- 尚未在打包后的 RC3 GUI 中完成真实词表导入操作；目前由解析自动化测试覆盖两种章节格式、别名、顺序、章节回退分组及 AI 输入外结果过滤。该人工验收保留在 Issue #43 中，不能写成已通过。
- Issue #40 的启动升级对话框自动测试失败沿用 RC2 基线，继续独立跟踪，不阻塞 RC3。
- GitHub 当前没有配置中的 CI 检查运行记录。

## 回滚

发现缺陷时不移动或复用 `v0.0.0-rc3` 标签；保留现有管理数据源目录，使用 `v0.0.0-rc2` 或后续修复候选版本。

## Comments

### 2026-10-24 发布完成

PR #44 已合入，RC3 已从合并后的 `main` 构建并发布。Issue #43 已追加构建证据、资产校验值和未验证项说明。
