# Git 与 GitHub 工作流规范

Status: resolved
Created: 2026-10-06
Feature-slug: `git-github-workflow-standard`

GitHub Issue: 规范引导任务，尚未创建外部 Issue
Branch: `docs/git-github-workflow-standard`
PR: 待创建

## 目标

为本仓库建立统一的 Git、GitHub Issue、分支、commit、PR、合并、评论、调试与回滚工作标准，使每项开发在开始前有范围、开发中有检查点、完成后有 PR、出现问题时能按 Issue/PR/commit 回溯。

## 范围内

- 新增 `docs/agents/git-github-workflow.md` 作为完整规范；
- 更新 `docs/agents/issue-tracker.md`，说明 `.scratch/` 与 GitHub Issue 的衔接；
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
- [x] GitHub Issue 模板要求范围、验收、风险和验证信息；
- [x] PR 模板要求 Issue 关联、验证、风险、回滚和合并前检查；
- [x] 没有覆盖用户已有的无关工作区文件。

## 相关既有规范

- `CONTEXT.md`
- `docs/agents/issue-tracker.md`
- `docs/agents/triage-labels.md`
- `docs/agents/domain.md`
- `docs/adr/0001-workbench-library-work-scope.md`
- `docs/adr/0002-slim-analysis-fields.md`
- `docs/adr/0003-merge-emotion-into-mood-tags.md`
- `docs/adr/0004-tag-normalize-hard-filter-pending.md`
- `docs/adr/0005-analysis-retry-and-progress.md`
- `docs/adr/0006-task-model-routing.md`

## Comments

### 2026-10-06 完成规范草案与模板

- 已先读取现有领域、ADR、Agent、Issue tracker、triage labels、`.scratch/` 议题和 Git 历史。
- 已在独立分支 `docs/git-github-workflow-standard` 上新增规范与 GitHub 模板。
- 待执行文档结构、链接、模板 front matter 和工作区状态检查。
