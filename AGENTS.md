## Agent skills

### Issue tracker

Issues and specs for this repo are tracked as local Markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Git and GitHub workflow

开发范围、分支隔离、commit 检查点、拉取请求评审、合并规则、GitHub Issue 模板、评论、调试和回滚，统一定义在 `docs/agents/git-github-workflow.md` 中。开始任何开发任务前都必须阅读该文件。面向项目的新增文档，以及 GitHub Issue、评论和 PR 内容，应优先使用简体中文；英文仅作为可选参照。

### Subagent workflow

调用 Subagent 前必须阅读 `docs/agents/subagent-workflow.md`。除搜索类任务外，主 Agent 必须先完成信息收集、现状判断和具体方案设计，再向 Subagent 提供可直接执行的完整指令；Subagent 不负责替主 Agent 规划范围或做未决策的架构选择。

### Triage labels

Use the five canonical triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo. Read `CONTEXT.md` and `docs/adr/` when they exist. See `docs/agents/domain.md`.