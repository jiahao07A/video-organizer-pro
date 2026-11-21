# 工作区整理、分支审查与主干同步

Status: in_progress
GitHub Issue: #85
Branch: feat/85-settings-center-redesign
Review base: 2fa278a76cfa7ca5f475cc4812a655d8e4c6fff5（当前origin/main）

## 目标与范围
用户授权清理工作区、提交或忽略文件、直接推送，并在有PR时审查合并。保留所有磁盘文件，通过gitignore排除本地产物，将本轮有价值的设置/配置/打包规格记录纳入Git。审查当前分支相对origin/main的设置中心与配置升级改动，验证后推送并通过PR合入main，最后快进本地main使其与远端一致。

## 现状
本地main与origin/main相同；当前分支领先两个提交：32fc3b3（配置兼容）、08a0221（设置中心）。远端没有打开的PR。78个未跟踪文件中有3个Markdown规格，其余为合成媒体、测试数据库、签名测试材料、探针脚本及构建边车文件。原文件不删除、不移动，不处理其他本地历史分支或独立worktree。

## 方案与验收
保持.scratch中的Markdown规格/验证记录可跟踪，其余产物默认忽略，并保留原有一次性Markdown草稿排除规则。固定审查命令为 `git diff 2fa278a76cfa7ca5f475cc4812a655d8e4c6fff5...HEAD`，按code-review技能分别核对仓库规范与Issue/spec。遵守用户不默认使用subagent的约束，由主Agent审查并复核之前审查证据。无阻断后创建或复用PR，遵守GitHub要求，不绕过保护、不强推main。最终验证HEAD、main、origin/main及远端refs/heads/main一致，工作区无待提交/未跟踪文件。

## 风险与回滚
ignore不删除现有产物；只提交已确认无凭据/真实数据的规格和gitignore变更。新默认规则仅限.scratch，不隐藏core、gui、tests或docs源码。合并前以固定SHA和测试结果为准，失败则停止合并并修复。若需要回滚代码，使用新分支revert并走PR，不覆盖用户数据。

## 任务清单
- [x] 获取远端状态、分支差异、现有PR与未跟踪文件清单。
- [x] 完成ignore规则和规格归档，确认无敏感/运行产物进入暂存区。
- [x] 完成规范与规格审查，处理阻断问题并完成回归。
- [ ] 提交整理变更，推送并创建/更新PR。
- [ ] 审查PR最终状态并合并，快进本地main。
- [ ] 验证本地/远端SHA一致与工作区干净。

## Comments
本轮仅整理及集成已完成的设置中心成果，不重新设计业务、不清理旧构建、不公开发布安装器。用户本条指令已明确授权本轮commit/push及审查后的合并，无需再次确认。

### 合并前审查（2026-11-21）
规范轴：复核独立分支、提交边界、配置备份/原子写入、错误脱敏与现有存储入口复用；未发现阻断违规。规格轴：复核九页职责、AI连接与任务模型分离、800ms多目标保存/失败重试、局部及全部恢复保持项、任务监控继承、维护停止扫描与未来配置拒写；未发现新的确定缺陷。本轮由主Agent执行两个维度的审查，不冒充新的独立批准；此前多轮独立审查记录见设置中心规格。

本地忽略规则仅允许scratch Markdown规格/验证记录进入版本管理，并保留原来历史产品规划目录和一次性草稿的排除。四份待归档规格已逐份阅读，凭据模式检查无命中；测试素材、私钥测试材料、数据库和构建输出未纳入。设置相关七个模块/测试的Ruff检查与git diff --check均通过。最新安装器166628728字节，SHA256仍为7dffa2ab2c1254f86b948e895c9522c3d3566ba10c5e62c3239d0d94bf21d0ab。

最终回归：19个设置、配置兼容、模型、监控、标签、备份、HTTP400、布局与更新测试模块，377 passed、30条原有Qt弃用告警，242.35秒。命令：`rtk proxy python -m pytest tests/test_config_compatibility.py tests/test_settings_persist.py tests/test_tag_config_groups.py tests/test_model_providers.py tests/test_media_watch.py tests/test_media_watch_workspace.py tests/test_backup_restore.py tests/test_review_bom_storage.py tests/test_tag_import_service.py tests/test_tag_group_ops.py tests/test_settings_catalog.py tests/test_model_selection.py tests/test_gui_review_regressions.py tests/test_layout_search_fixes.py tests/test_review_responsive_layout.py tests/test_recovery_controls.py tests/test_review_watch_recovery.py tests/test_review_recovery_busy_gate.py tests/test_software_update_gui.py -q`。

本文件为提交前的审查快照；推送、PR、合并提交及最终SHA核对将追加到Issue85与同目录被忽略的merge-verification.md，避免合并后为机器状态记录再次改动主干。
