## Agent 技能

### 议题追踪

本仓库的 Issue 和规格记录保存在 `.scratch/<feature>/` 下。详见 `docs/agents/issue-tracker.md`。

### Git 和 GitHub 工作流

开发范围、分支隔离、commit 检查点、拉取请求评审、合并规则、GitHub Issue 模板、评论、调试和回滚，统一定义在 `docs/agents/git-github-workflow.md` 中。开始任何开发任务前都必须阅读该文件。面向项目的新增文档，以及 GitHub Issue、评论和 PR 内容，应优先使用简体中文；英文仅作为可选参照。

### Subagent 工作流

调用 Subagent 前必须阅读 `docs/agents/subagent-workflow.md`。除搜索类任务外，主 Agent 必须先完成信息收集、现状判断和具体方案设计，再向 Subagent 提供可直接执行的完整指令；Subagent 不负责替主 Agent 规划范围或做未决策的架构选择。

### Triage 标签

使用五个规范 triage 标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human` 和 `wontfix`。详见 `docs/agents/triage-labels.md`。

### 领域文档

这是一个单一上下文仓库。相关文件存在时，开始工作前必须阅读 `CONTEXT.md` 和 `docs/adr/`。详见 `docs/agents/domain.md`。
