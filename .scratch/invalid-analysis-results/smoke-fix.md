# Issue #73 安装冒烟退出修复

Status: ready-for-review
GitHub Issue: #73
Branch: fix/73-invalid-analysis-results
Base: 61f8ee8967ee00b72ba95598cfcb1237ac65c2c7

## 背景与证据

既有隔离安装、更新启动核对、再次安装及数据保留成功，但 `close_test_window` 发送 WM_CLOSE 后等待进程退出超时。`gui/main_window.py` 的 closeEvent 在托盘可用时明确执行 hide、ignore，只有 `_exit_application` 代表“彻底退出”。原测试把两种语义混同；现有 test_close_hides_to_tray_and_exit_stops_monitor 已验证关闭隐藏行为。

## 范围与方案

只修正隔离安装冒烟退出触发与等待，不改变普通关闭隐藏到托盘的产品行为、不强制终止后台业务任务。应用入口增加显式 `--smoke-exit`，在启动核对后调度正式 `_exit_application`；等待更新空闲。烟测通过 `--background --smoke-exit` 启动，前者避免改动 Windows 登录自启注册项。读取已完成启动核对应早于检查快速退出，避免正常退出与父进程轮询竞争。真实退出码必须为 0。

范围外：真实素材、用户配置、正式安装、清理旧构建。用户已于 2026-11-14 明确要求提交、合并和发布；RC8 标签已占用，本 PR 将版本递增至 RC9。签名和发布仅对合并后 main 构建的资产执行。

## 验收与验证

- 自动化等待更新检查/准备忙碌后走正式退出入口。
- 启动核对未完成即退出、启动核对超时、退出码非零仍然失败，不放宽验收。
- 原有关闭隐藏到托盘行为保持；真实 Qt 正式退出路径停止监控。
- 使用全新目录 PyInstaller 构建并编译普通及隔离安装器。
- 完整运行 tools/smoke_installer_update.py，首次安装/启动/正常退出/覆盖安装/合成设置与记录保留全部通过。
- 新子代理分别按规格和规范只读审查；由主 Agent 修复，再请新子代理复审，直到无待修发现。

## 风险与回滚

自动退出参数仅显式传入时启用。启动失败、任务未完成或退出失败不能通过烟测；测试超时保留隔离进程供诊断，不自动结束用户进程。修改回滚为本轮入口、脚本、测试的精确差异，不涉及数据迁移。新构建保留旧目录，不覆盖或删除。

## 任务清单

- [x] 核对工作区、流程、ADR、现有证据及产品关闭语义
- [x] 增加退出调度与父进程竞态回归
- [x] 补充退出调度与现有 Qt 托盘行为的定向验证
- [x] 第一轮全新规格、规范 Subagent 只读审查
- [x] 第二轮全新规格、规范 Subagent 只读复审
- [x] 主 Agent 移除第二轮发现的遗留 WM_CLOSE 关闭实现
- [x] 修复后新 Subagent 复审直至无待修复发现
- [x] 全新普通与隔离包构建、完整安装冒烟
- [x] 最终回归、校验和与验收记录
- [x] 用户明确批准 commit、merge、release
- [ ] 由非作者审阅者批准 PR 并合入 main
- [ ] 合并后构建、签名、隔离复验并公开发布 RC9

## Comments

### 审查执行包

审查范围限定 video_organizer_app.py、tools/smoke_installer_update.py、tests/test_smoke_exit.py、本记录，以及本轮新增正式退出测试。以 `git diff 61f8ee8 -- <上述文件>` 加未跟踪测试为本轮差异。两轴独立审查，均不得修改文件、安装、打包、提交、发布、使用用户数据或真实网络 API。重点检查托盘语义、启动/退出时序、正常退出门禁、测试隔离、误报/漏报及范围。主 Agent 独立复核所有报告。

第二轮规格轴无发现；规范轴确认 `close_test_window` 已无调用但仍保留发送 `WM_CLOSE` 后等待进程退出的旧逻辑。全仓搜索证实只有定义、没有调用。主 Agent 已删除该死代码及专用 `ctypes` import；普通托盘关闭行为继续由现有 Qt 回归覆盖。

### 第三轮审查与最终验证

第三轮全新规格、规范审查均为 0 项待修发现。修复后复跑 `python -X utf8 -m pytest tests/test_smoke_exit.py tests/test_gui_review_regressions.py::test_close_hides_to_tray_and_exit_stops_monitor -q`：6 passed。`python -m compileall -q video_organizer_app.py tools/smoke_installer_update.py tests/test_smoke_exit.py`、Ruff 及 `git diff --check` 通过。

PyInstaller 6.19.0 / Python 3.13.12 使用全新 work/dist 输出完成 `build_app.spec` onedir 构建。普通和隔离安装器均成功生成，版本 `0.0.1-rc8`。隔离安装器完整冒烟命令：`python -X utf8 tools/smoke_installer_update.py --installer .release-build/issue73-smokefix-20261113/isolated-installer/VideoOrganizer-v0.0.1-rc8-isolated-smoke.exe --output .release-build/issue73-smokefix-20261113/smoke`，结果 `installed_version=0.0.1-rc8`、`startup_exit_code=0`、`update_session=completed`、`custom_data_preserved=true`、`record_count=1`、`reinstall_preserved_settings=true`。本次首次安装、应用正式退出、二次覆盖安装全部通过。

该 RC8 包只记录为源码改动前的本地冒烟证据，不作为本次 RC9 发布资产；RC9 资产须在 PR 合入 main 后重新构建、签名及隔离验收。

包内未发现 `.env`、settings.json、tag_config.json、video_organizer.db、SQLite 文件或视频素材。普通安装器 SHA-256：`2E4AB625F095EBDCAA6364A4888E599FF79959C973F107C638BF44C4EBD2D858`；隔离冒烟安装器 SHA-256：`DE2B177D1D084E1B5D7FF85F5F1886EA76CC71D3B5B3059EB48B0AD024D82A57`。隔离安装器仅用于本地验收，禁止发布。未发布、未安装到正式目录、未触碰真实素材、未提交。等待用户验收与批准 commit。
