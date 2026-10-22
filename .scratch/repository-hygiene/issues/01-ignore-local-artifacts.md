# 本机数据与构建产物忽略规则

Status: claimed
GitHub Issue: #34 — https://github.com/jiahao07A/video-organizer-pro/issues/34
Branch: `chore/34-ignore-local-artifacts`
PR: [#36](https://github.com/jiahao07A/video-organizer-pro/pull/36)

## 背景与目标

仓库根目录下的运行数据、数据目录定位记录和本机构建尝试结果不属于源代码交付，但未被忽略时会持续出现在 Git 状态中，并增加误提交风险。本任务只增加精确的仓库级忽略规则，不删除或移动本机文件。

## 范围内

- 忽略根目录 `data/`、`.video-organizer-data.json`、`build-attempt/` 和 `dist-attempt/`。
- 保持现有 `logs/` 忽略行为，不改变 PR #32 中人工测试日志占位文件规则的设计。
- 添加回归检查，验证这些路径被忽略且附近的源文件仍可跟踪。

## 范围外

- 不删除、移动或改写本机数据库、备份、日志或构建目录。
- 不忽略 `词_优化版.txt`；其是否为正式词表资产仍待确认。
- 不改变现有 `.thumbnails/`、正式 `build/`、`dist/` 或备份规则。

## 验收标准

- `git check-ignore -v` 显示上述四类路径匹配明确规则。
- 不忽略用户词表草稿和工程源文件。
- `git diff --check` 通过，PR 只包括忽略规则和本议题记录。

## 风险与回滚

忽略规则过宽可能隐藏未来应提交的样例，因此仅匹配仓库根目录下的固定目录或定位文件。回滚时移除对应条目，不影响任何本机文件。

## 验证计划

使用 `git check-ignore -v data/… .video-organizer-data.json build-attempt/… dist-attempt/…` 检查新增仓库级规则。词表草稿没有加入共享 `.gitignore`；当前克隆通过 `.git/info/exclude` 单独过滤它，文件保留在本机且该设置不会传播。运行 `git diff --check`。
