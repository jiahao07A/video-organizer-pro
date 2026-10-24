# 标签词表导入格式解析修复

Status: resolved
GitHub Issue: [#42](https://github.com/jiahao07A/video-organizer-pro/issues/42)
Branch: `fix/42-tag-import-format`
PR: [#44](https://github.com/jiahao07A/video-organizer-pro/pull/44)

## 背景与目标

导入 `词.txt`、`词_优化版.txt` 时，旧导入入口将说明、分组标题和“标准词 ← 别名”整行作为待分类标签；AI 分类结果还可能包含输入中不存在的额外标签。目标是按词表格式读取标准词，保留顺序与章节组信息，并将别名写入既有同义词表。

## 范围

### 范围内

- 忽略空行、注释、分组标题和结构化章节前的说明。
- 解析 `# === 氛围 mood ===` 与 `一、主体：...` 等章节形式。
- 解析 `←` / `<-` 别名，并仅将标准词送入分类、导入标准词表。
- 在 AI 分类结果中仅接受本次输入的标准词。
- 导入后将别名登记为标准词的同义词。

### 范围外

- 不修改词表源文件、标签归一规则或待审词审批语义。
- 不更改 AI 分类模型与其它标签库操作。
- 不在未合并的分支上制作或发布正式候选包。

## 验收与验证

- 两种章节格式中的注释和标题不作为标签，别名不作为第二个标准词，顺序保持稳定。
- 章节来源组作为 AI 未返回某个输入词时的回退分组。
- AI 返回输入外键时忽略该键。
- 相关标签导入、标签词表和标签组操作测试通过；发布另由 Issue #43 跟踪。

## 风险与回滚

解析使用现有 `core.tag_vocab` 词行及分组标题解析器，并补充编号章节识别。若出现误解析，可通过独立修复提交恢复旧入口；不会改写原始词表。

## Comments

### 2026-10-24 实现与验证

复现日志确认 `词.txt` 的 11 行注释、5 个分组标题、21 行别名格式曾全部进入 AI 分类，分类输入为 178 条而分类结果达到 194 条。已增加解析、章节回退分组、别名持久化和输入外 AI 结果过滤。

验证：`python -m compileall -q core gui tests video_organizer_app.py video_organizer_pyside6.py` 通过；`python -m pytest -q -k "not test_startup_upgrade_dialog_confirms_and_reports_progress"` 为 `408 passed, 1 deselected`；`git diff --check` 通过。提交 `0b4b326` 已推送，PR #44 等待非作者审阅、必要 CI 和重新打包后的人工导入验收。

### 2026-10-24 合并与 RC3

PR #44 已通过主 Agent 审阅并以 Rebase and merge 合入 `main`，合并提交为 `830b12bfbd55e40b1bdacfd6a6eb3764171896fb`；Issue #42 已自动关闭。后续 RC3 从合并后的 `main` 构建并发布，记录见 `.scratch/windows-rc3-release/spec.md`。

验证补充：相关测试 `18 passed`；完整回归 `408 passed, 1 deselected`；隔离构建、ZIP 审计和启动/退出冒烟通过。Windows RC3 包内真实词表导入仍未人工验证，已在 Issue #43 和 RC3 发布说明中明确列为未验证项。
