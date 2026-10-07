# Git 与 GitHub 工作流规范

Status: resolved
Created: 2026-10-06
Feature-slug: `git-github-workflow-standard`

GitHub Issue: #1 (https://github.com/jiahao07A/video-organizer-pro/issues/1)
Branch: `docs/git-github-workflow-standard`
PR: #2 (https://github.com/jiahao07A/video-organizer-pro/pull/2)

## 目标

为本仓库建立统一的 Git、GitHub Issue、分支、commit、PR、合并、评论、调试与回滚工作标准，使每项开发在开始前有范围、开发中有检查点、完成后有 PR、出现问题时能按 Issue/PR/commit 回溯。

## 范围内

- 新增 `docs/agents/git-github-workflow.md` 作为完整规范；
- 新增 `docs/agents/subagent-workflow.md`，规定 Subagent 的调用时机、调用前信息门槛、执行指令、分支/工作区、验证和审查责任；
- 更新 `docs/agents/issue-tracker.md`，说明 `.scratch/` 与 GitHub Issue 的衔接；
- 更新 `docs/agents/triage-labels.md`，将规范标签说明统一为简体中文并保留标签原文；
- 更新 `docs/agents/domain.md`，将领域文档规则统一为简体中文并保留英文术语参照；
- 新增 `.github/ISSUE_TEMPLATE/` 下的 Bug、Feature、Task、Docs 模板和配置；
- 新增 `.github/pull_request_template.md`；
- 检查新文档与 `CONTEXT.md`、`docs/adr/`、既有本地议题约定的一致性。

## 范围外

- 不修改产品代码、数据库、标签规则或已有 ADR；
- 不清理工作区原有未跟踪脚本、压缩包或其他本地文件；
- 不配置 GitHub 分支保护、CI 必需检查或远程凭据；
- 不改写现有分支历史，不通过本地合并绕过文档 PR。GUI 的一次性主干集成授权与结果记录在 Issue #3，本 PR 不包含该产品代码改动。

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
- [x] 本地文档与模板已完成，GitHub Issue #1 与 PR #2 已创建；本轮审阅和合并前验证完成后按维护者授权通过 GitHub 合并。

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

## 下一步

原进入远程协作的步骤已执行。2026-10-07 维护者明确授权本轮复核通过后直接通过 GitHub 合并 PR #2；不修改未来默认 PR 规则：

- [x] 创建 GitHub Issue，补充 Issue 编号并链接本地 spec；
- [x] 推送 `docs/git-github-workflow-standard` 分支；
- [x] 创建 Draft PR，填写中文优先的 PR 正文并链接 Issue；
- [x] 按维护者本轮授权完成规范与规格终审，文档及模板可通过 GitHub 合并；
- [x] 本地记录已提供 PR #2 与 Issue #1 的永久链接；最终合并 SHA 与关闭总结在对应远程记录中追加，避免提交内自引用尚未生成的 SHA。

## Comments

### 2026-10-07 推送分支并创建 Draft PR

- 已将 `docs/git-github-workflow-standard` 推送到 `origin`。
- 已创建 Draft PR [#2](https://github.com/jiahao07A/video-organizer-pro/pull/2)，目标分支为 `main`，源分支为 `docs/git-github-workflow-standard`。
- PR 使用中文优先正文并通过 `Fixes #1` 关联 GitHub Issue #1。
- 当前等待维护者人工审阅；未执行合并。

### 2026-10-07 创建 GitHub Issue

- 已创建 GitHub Issue [#1](https://github.com/jiahao07A/video-organizer-pro/issues/1)：`[Docs] 建立 Git、GitHub 与 Subagent 工作规范`。
- Issue 使用 `needs-triage` 标签，正文已写明目标、范围、验收标准、风险、回滚和本地 spec 路径。

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

### 2026-10-06 根据 review 优化文档结构

- 将 `AGENTS.md` 的全部入口说明统一为简体中文优先。
- 将完整的 GitHub Issue、评论、分支、commit、PR、合并、调试和回滚规则保留在 `git-github-workflow.md`，`issue-tracker.md` 只保留 `.scratch/` 的本地目录、字段和追加方式。
- 暂不在 `.github/ISSUE_TEMPLATE/config.yml` 添加 `contact_links`，避免在规范尚未合入远程 `main` 前提供可能失效的链接。
- 下一步批准路径：创建 GitHub Issue → 推送当前分支 → 创建 Draft PR → 由维护者人工审阅。

### 2026-10-07 本轮终审、验证记录与合并交接

- 维护者明确要求先将 GUI 独立修复并直接集成主干，再复核 PR #2，通过后直接在 GitHub 合并。
- GUI 已作为单一提交 `269961bb92974f6a1b6daff94147597a117c72a5` 集成到远程 `main`，验证与一次性授权见 Issue #3；此授权不改变未来默认工作流规则。
- 文档分支以 merge 同步 `main`，冲突中的产品文件全部保留已验证主干版本，不重写共享分支历史。最终 PR 净差异只有原范围内的 13 个文档/模板/入口文件。
- 本轮复核分别对照仓库规范与 Issue #1/spec。补齐 Bug 模板修复范围、风险回滚和验证计划，Docs 模板风险回滚，Feature 模板验证计划；同步已过时的 Issue/PR 创建状态和 Docs 模板范围。
- 13 个文件的 YAML/front matter、四类模板字段、13 个 Markdown 相对链接、代码围栏和行尾空白检查通过。
- 同步主干后本轮重新运行完整 `python -m pytest -q`：234 通过，130.19 秒；产品代码相对 `origin/main` 无差异。产品原始素材、数据库和个人配置未进入提交。
- 最终审阅：规范维度与规格维度均无剩余阻断问题；没有声称取得独立 GitHub approval 或完成多人实操/分支保护/CI 配置验收。
- 采用 GitHub squash merge：PR 历史包含已由主干单提交整合替代的旧 GUI 提交，不使用 rebase 重放旧 GUI；仅合并已审阅的文档净差异。
- 合并结果和完整 SHA 在 [PR #2](https://github.com/jiahao07A/video-organizer-pro/pull/2) 与 [Issue #1](https://github.com/jiahao07A/video-organizer-pro/issues/1) 的关闭总结中追加。本文件状态表示文档实现与本轮验证完成；远程合并以 GitHub 记录为准。
- 回滚：对 PR #2 的 squash commit 使用新的 revert 分支/PR；不回滚独立 GUI 提交。遗留限制：真实多人协作、分支保护和 CI 必需检查尚未配置/实操验收，不作为本轮已完成内容。
