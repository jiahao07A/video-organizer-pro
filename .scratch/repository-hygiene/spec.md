# 仓库本机状态文件治理

Status: claimed
GitHub Issue: #34 — https://github.com/jiahao07A/video-organizer-pro/issues/34
Branch: `chore/34-ignore-local-artifacts`
PR: 待创建

## 目标和背景

防止本机运行数据、便携数据目录定位记录与临时构建结果进入版本控制，同时保留开发所需源文件和待确认的词表草稿。

## 范围内

- 仓库根目录 `data/`、`.video-organizer-data.json`、`build-attempt/`、`dist-attempt/`。
- 采用固定根路径匹配，避免误屏蔽子目录中未来明确纳入版本库的样例。
- 使用 `git check-ignore` 验证规则边界。

## 范围外

- 不清理任何磁盘文件，不改动用户数据。
- 不把 `词_优化版.txt` 纳入提交或加入共享忽略规则。
- 不更改日志规则及其他已有构建规则。

## 验收标准

- 上述本机目录和文件不再作为未跟踪内容提示。
- 源代码、规格文档、人工测试占位文件仍按照现有规则处理。
- 规则清晰可撤销，`git diff --check` 通过。

## 依赖、风险与回滚

不涉及运行行为或数据迁移。若目录将来成为受版本控制的样例数据，应通过显式例外规则纳入；回滚本次忽略条目即可。

## 验证计划

使用 `git check-ignore -v` 检查匹配路径与规则来源，并确认词表草稿不匹配新规则。
