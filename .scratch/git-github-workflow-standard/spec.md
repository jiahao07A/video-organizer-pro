# Git 与 GitHub 工作流规范

Status: ready-for-human
Created: 2026-10-06
Feature-slug: `git-github-workflow-standard`

GitHub Issue: 规范引导任务，尚未创建外部 Issue
Branch: `docs/git-github-workflow-standard`
PR: 待创建

## 目标

为本仓库建立统一的 Git、GitHub Issue、分支、commit、PR、合并、评论、调试与回滚工作标准，使每项开发在开始前有范围、开发中有检查点、完成后有 PR、出现问题时能按 Issue/PR/commit 回溯。

## 范围内

- 新增 `docs/agents/git-github-workflow.md` 作为完整规范；
- 新增 `docs/agents/subagent-workflow.md`，规定 Subagent 的调用时机、调用前信息门槛、执行指令、分支/工作区、验证和审查责任；
- 更新 `docs/agents/issue-tracker.md`，说明 `.scratch/` 与 GitHub Issue 的衔接；
- 更新 `docs/agents/triage-labels.md`，将规范标签说明统一为简体中文并保留标签原文；
- 更新 `docs/agents/domain.md`，将领域文档规则统一为简体中文并保留英文术语参照；
- 新增 `.github/ISSUE_TEMPLATE/` 下的 Bug、Feature、Task 模板和配置；
- 新增 `.github/pull_request_template.md`；
- 检查新文档与 `CONTEXT.md`、`docs/adr/`、既有本地议题约定的一致性。

## 范围外

- 不修改产品代码、数据库、标签规则或已有 ADR；
- 不清理工作区原有未跟踪脚本、压缩包或其他本地文件；
- 不代替维护者创建、配置或关闭远程 GitHub Issue；
- 不改变现有分支历史或直接合并到 `main`。

## 验收标准

- [x] 规范覆盖开发前范围、任务打包/拆分、Issue、评论、分支、commit、PR、合并、调试和回滚；
- [x] 规范明确主干禁止直接开发，所有功能通过分支和 PR；
- [x] 规范明确 commit 检查点、消息格式、敏感信息和生成产物边界；
- [x] 规范明确 GitHub Issue 与 `.scratch/` 的双向链接和同步责任；
- [x] 规范明确 Subagent 的调用边界：搜索类任务可以提前委派，其他任务必须由主 Agent 先调查并制定完整执行方案；
- [x] Subagent 规范要求执行指令具体到文件、符号、行为、步骤、验证、风险和返回格式；
- [x] Subagent 规范明确主 Agent 对实际差异、验证、Issue/PR 记录和最终提交承担责任；
- [x] GitHub Issue 模板要求范围、验收、风险和验证信息；
- [x] PR 模板要求 Issue 关联、验证、风险、回滚和合并前检查；
- [x] 没有覆盖用户已有的无关工作区文件。

## 相关既有规范

- `CONTEXT.md`
- `docs/agents/issue-tracker.md`
- `docs/agents/subagent-workflow.md`
- `docs/agents/triage-labels.md`
- `docs/agents/domain.md`
- `docs/adr/0001-workbench-library-work-scope.md`
- `docs/adr/0002-slim-analysis-fields.md`
- `docs/adr/0003-merge-emotion-into-mood-tags.md`
- `docs/adr/0004-tag-normalize-hard-filter-pending.md`
- `docs/adr/0005-analysis-retry-and-progress.md`
- `docs/adr/0006-task-model-routing.md`

## Comments

### 2026-10-06 增加简体中文优先规则

- 规则适用于 GitHub Issue 标题与正文、Issue 评论、PR 标题与正文、`.scratch/` 规格记录和今后新增或大幅修改的 `docs/` 文档。
- 简体中文是权威版本；英文仅作为可选参照。
- GitHub 标签、Conventional Commits 字段、分支名、命令、路径、代码符号和原始错误消息保留原格式。
- 已更新工作流正文、`AGENTS.md`、Issue/PR 模板、本地 Issue tracker 说明、`triage-labels.md` 和 `domain.md`。
### 2026-10-06 增加 Subagent 调用规范

- 新增 `docs/agents/subagent-workflow.md`。
- 明确搜索类任务是唯一可以在主 Agent 未完成完整方案前委派的例外。
- 明确功能开发、debug、fix、重构、测试和文档修改等任务，必须由主 Agent 先调查、决策并编写完整执行包，再交给 Subagent。
- 明确 Subagent 不负责重新规划范围或决定未决策的架构方案；主 Agent 必须复核差异、验证结果并负责最终提交和 PR。
- 已将 Subagent 规范接入 `AGENTS.md` 和 Git/GitHub 工作流入口。

### 2026-10-06 根据 review 修复本地记录

- 将本地 spec 状态从 `resolved` 调整为 `ready-for-human`，因为远程 Issue 和 PR 尚未创建，尚未满足最终关闭条件。
- 将实际修改过的 `docs/agents/triage-labels.md` 与 `docs/agents/domain.md` 补入“范围内”清单。
- 本次修复只更新本地工作流记录，没有修改产品代码或开始任务时已有的未跟踪文件。
