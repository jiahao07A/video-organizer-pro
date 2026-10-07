# 本地 Markdown 议题追踪

本仓库的 Issue 和规格记录保存在 `.scratch/` 中。

GitHub Issue 是共享的任务身份和协作记录；详细执行记录继续保存在 `.scratch/`。除一次性小修正外，每个需要 GitHub Issue 或多个 commit 的任务都应将两类记录相互链接。完整的 Git/GitHub 工作流见 [`git-github-workflow.md`](./git-github-workflow.md)。

## 目录约定

- 每个功能使用一个目录：`.scratch/<feature-slug>/`
- 功能规格文件为：`.scratch/<feature-slug>/spec.md`
- 每个实现议题一个文件，路径为 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，编号从 `01` 开始
- GitHub Issue 使用 [`triage-labels.md`](./triage-labels.md) 中的五个规范 triage 标签之一
- 本地 Markdown 议题在文件顶部附近使用 `Status:` 行记录执行状态
- 评论和对话历史追加在文件底部的 `## Comments` 标题下
- 存在 GitHub Issue 时，在本地 spec 中记录 `GitHub Issue: #N`，并在 GitHub Issue 中链接本地 spec 路径
- 已创建分支或 PR 时，在本地 spec 或对应议题中记录 `Branch:` 和 `PR:`

## GitHub Issue 与本地记录的分工

- **GitHub Issue**：任务身份、共享范围、讨论、方案决定、PR 关联和关闭总结。
- **`.scratch/` spec**：完整目标、范围内/范围外、拆分顺序、依赖、验收矩阵和交接信息。
- **`.scratch/` 子议题**：一个可以独立实现和验证的执行单元。

这两类记录应相互链接，不能形成互相竞争的事实来源。如果两边内容不一致，先在 GitHub Issue 中记录最新决定，再更新本地 spec 或议题，之后才能继续实现。

## 什么时候需要本地记录

以下任务只要规模足以需要 GitHub Issue 或多于一个 commit，就必须创建或更新本地 spec：新功能、bug 修复、重构、性能任务、数据/文件操作、依赖/构建变更，以及工作流或文档规范变更。单行拼写修正或同一范围内的格式修正，可以记录在上级记录或 PR 正文中。

只有在用户结果、验收标准、风险、回滚方式和评审人都一致时，才可以将小任务打包。若结果、风险、依赖、评审人或交付时机不同，应拆分为子议题。

## 本地记录必须包含的内容

实现开始前，spec 或子议题应明确写出：

- 目标和背景
- 范围内与范围外
- 验收标准
- 依赖与约束
- 风险与回滚
- 验证计划
- GitHub Issue、分支和 PR 链接（已有时）

实现议题继续使用现有的 `Status:` 行；存在依赖时，使用 `Blocked by: NN, NN` 行记录阻塞关系。

## 工具约定

当某项技能要求“发布到议题追踪器”时，在 `.scratch/<feature-slug>/` 下创建文件；目录不存在时一并创建。

当某项技能要求“读取相关议题”时，读取被引用的 Markdown 文件。用户通常会直接提供文件路径或 Issue 编号。

## 评论和交接格式

重要变化追加在 `## Comments` 下。建议使用以下明确的小标题：

- `状态更新`
- `范围决定`
- `方案决定`
- `验证记录`
- `问题`
- `交接`

交接记录应包含当前分支、最后一个 commit、已完成的检查、剩余工作、已知风险和下一步动作。不得通过静默改写来掩盖过去的决定。工作完成时，在将本地记录标记为 resolved 之前，应写明 PR、合并 commit、验证结果和未解决限制。

新增的本地评论、交接说明和 `.scratch/` 规格记录，应优先使用简体中文。英文可以作为参照放在后面，但简体中文是权威版本。命令、路径、分支名、Issue 标签、commit 标识、代码符号和原始错误消息保持原格式。

完整的 Issue、评论、分支、commit、PR、合并、调试和回滚规则见 [`git-github-workflow.md`](./git-github-workflow.md)；本文件只规定 `.scratch/` 本地记录的目录、字段和追加方式。

## Wayfinding 操作

以下操作供 `/wayfinder` 使用：

- Map：`.scratch/<effort>/map.md`
- 子议题：`.scratch/<effort>/issues/NN-<slug>.md`
- 记录阻塞：使用 `Blocked by: NN, NN` 行
- Frontier：按编号选择第一个开放、未阻塞且未认领的议题
- Claim：将 `Status:` 设置为 `claimed` 后再开始工作
- Resolve：追加结论，将 `Status:` 设置为 `resolved`，并更新 map
