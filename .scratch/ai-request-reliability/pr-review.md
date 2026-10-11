# PR #5 评审记录

日期：2026-10-11
GitHub Issue: #4
PR: https://github.com/jiahao07A/video-organizer-pro/pull/5
评审基线：58b6dc2d5286048216bcacb4c5d56796d4026821
评审 HEAD：bf9be1680a22c1006f5eba37748b02d77b169f18
结论：存在 3 项实质合并阻塞问题，保持 PR 和 Issue 开放，未发起合并。

## 规范核对（Standards）

### 1. P1：转录前处理失败会删除已有音频

`core/video_organizer_service.py:1142-1145` 的新增 `finally` 清理固定路径 `video_path + ".mp3"`，没有确认该文件由本次创建。当路径已存在且 `ffmpeg -version` 失败时，尚未开始抽取就删除原有音频。主干基线在相同输入下保留该文件，本 PR 会删除，属于新增数据丢失风险。

违反 `docs/agents/git-github-workflow.md` §2.7 的原始素材保护要求。修复应使用本次独占的临时路径，并且仅清理本次拥有的文件；不要通过跳过清理或覆盖既有文件掩盖问题。需补齐 ffmpeg 不可用、抽取失败、转录失败和同名文件已存在的回归测试。

### 2. P2：鉴权状态码被文本判定覆盖，误触发重试

`core/ai_gateway.py:373` 和 `:442` 使用 `is_retriable(error) or is_retriable(message)`。对 `status_code=401` 且消息含 `timeout` 的异常，异常对象判定正确返回 False，后续文本启发式却返回 True。默认配置实际提交 3 次聊天请求，最终失败也标为可重试；主干基线只提交 1 次并标为不可重试。

违反 ADR-0005 的鉴权、文件等不可恢复错误不重试规则。新增音频路径还可以复现：缺失文件名含 `timeout` 时，FileNotFoundError 被判为可重试并尝试 3 次。修复应让明确 HTTP 状态码与文件错误类型优先于文本启发式，不允许文本再次推翻不可恢复判定；聊天和音频必须共同覆盖。

### 3. P2：配置切换时混用不同路由的模型与连接

`core/ai_gateway.py:299-303` 先解析任务路由取得模型，随后 `client_for_task()` 在第 219 行再次解析路由。若两次解析之间从供应商 A/model-a 切到 B/model-b，会向 B 的连接发送 model-a；主干基线通过一次 `client_for_task()` 的同一返回值取得连接和模型，不会混用。

违反 ADR-0006 关于按任务指定供应商和模型，以及配置变化不破坏进行中请求的要求。使用可注入 resolver 模拟两次解析间切换，已复现调用组合 `('b', 'model-a')`。修复应从同一次路由快照取得客户端与模型；可以继续使用旧快照或使用新快照，但禁止混用。需新增路由热切换和进行中请求不被中断的测试。

规范轴共 3 项实质问题，最高 P1。独立规范复核另提示聊天与音频失败处理重复；这是非阻塞的维护性建议，不另计合并阻塞项。

## 规格核对（Spec）

独立规格复核发现 2 项问题，与上述第 1、2 项重合：

- 第 1 项违反本地 spec 的“现有行为不回退”。
- 第 2 项违反“鉴权、用户取消和文件错误不重试；瞬时错误仍按 ADR-0005 上限重试”。

规格轴共 2 项，最高 P1；未发现其他证据充分的范围扩张或规格缺失。上述第 3 项由规范轴发现，并由主审动态复核，不改写独立规格复核的计数。

## 主审验证

- `git diff --check origin/main...HEAD`：通过。
- `py -m compileall -q core gui tests`：通过。
- PR 声明的 11 个测试模块：92 passed。
- `py -m pytest -q`：6 个 GUI 测试模块因缺少 PySide6 无法收集；2 skipped。不是完整测试通过。
- 排除上述 6 个不可收集模块后运行所有现有测试：175 passed, 2 skipped。
- `py -m pytest -q .scratch/ai-request-reliability/pr_review_repro_test.py`：4 failed, 3 passed。4 个失败分别覆盖鉴权误重试、误删除既有音频、缺失音频误重试、路由快照混用；3 个通过为直接提取主干相应类/方法后的对照用例。

排除 GUI 模块的完整命令：

`py -m pytest -q --ignore=tests/test_analysis_tagging_bugs.py --ignore=tests/test_column_prefs_migrate.py --ignore=tests/test_list_numbers_refresh.py --ignore=tests/test_ops_filter_scope.py --ignore=tests/test_tag_ai_worker.py --ignore=tests/test_tag_normalize.py`

全部动态复现只使用 fake client、假凭据、example.invalid 地址和 pytest 临时目录，没有调用真实 AI、读取真实素材或修改业务代码。配置切换复现为注入式确定性模拟，未声称真实 GUI 并发测试已完成。

## 后续与回滚

先在现有 PR 范围内修复三项问题、把复现转成正式回归测试，再复跑现有测试与边界测试并重新评审。本次仅留下评审记录和复现脚本，没有修复提交、推送或合并，不涉及数据迁移。

GitHub 评审及 3 条行内意见已成功发布：https://github.com/jiahao07A/video-organizer-pro/pull/5#pullrequestreview-5481616149 。随后向 Issue #4 追加评论与同步标签的尝试因 GitHub 网络超时失败，GitHub CLI 与备用接口均未成功；未执行标签更改。需在网络恢复后补同步 Issue，不能将本地记录视为已完成远程同步。

## 修复跟进（2026-10-11）

用户要求继续后，已将方案记录于 `pr-fix-plan.md` 并同步 Issue #4，网络恢复后的议题已标为 `ready-for-agent`。主审实施并推送三个独立修复提交：

- `cfd7b6c`：独占临时音频，解决既有文件误删除；正式音频回归先失败再修复，6 个边界用例全部通过。
- `817b51a`：原异常只判一次，不可恢复状态码/类型优先；聊天与音频新增 11 项错误边界，纯策略新增 34 项边界，均通过。
- `75066f5`：同一路由快照获取模型和连接；新增 4 项路由切换、reload 期间进行中请求、后续新连接和单次解析测试，均通过。

主审验证：原评审脚本从 4 failed, 3 passed 转为 7 passed；正式可运行回归从 175 passed, 2 skipped 扩展到 230 passed, 2 skipped；完整套件仍有 6 个 GUI 模块因缺少 PySide6 无法收集。语法、diff 与全部变更文件静态诊断通过，未使用真实服务或素材。

三条行内意见已追加修复 commit 与验证记录；独立规范/规格复审固定于 `75066f5d2c83b8804af815bff88fafa23b69ea33`，结论待主审复核。原评审结论保留为历史事实，修复后的合并结论另行追加，不覆盖前次记录。

## 修复后复审结论

### Standards

0 项实质阻塞，原三项问题的修复成立；独立规范复核及主审均未发现新增的素材保护、错误分类、线程和路由一致性阻塞。1 项非阻塞维护性建议为聊天/音频的重试终止代码重复，目前行为一致、测试通过，后续需要调整策略时再考虑小范围抽取。

### Spec

0 项规格遗漏/部分实现、0 项范围越界、0 项已实现但错误的行为。活动请求网关、音频 content_description+whisper-1、六槽、人工确认写库、冷启动默认停用均保持。原三项阻塞已解决。

### 主审接收与合并判断

主审检查固定提交的实际差异，并验证 230 passed, 2 skipped 与原复现 7 passed，接收两项只读复审结论。固定代码提交为 `75066f5d2c83b8804af815bff88fafa23b69ea33`；后续仅归档文档与复现记录。三条原意见将标记解决。

用户已授权“审核无问题后直接合并”，本次继续修复后已无实质阻塞。当前 GitHub 身份与 PR 作者相同，不能提交自我批准；以 COMMENT 留下独立复审与维护者授权证据，合并仍须通过 GitHub PR、遵守必需检查和分支保护，不使用管理者绕过。GUI/真实服务未验证的限制保持明确记录。合并结果待追加。

总结：Standards 0 项阻塞（1 项非阻塞维护性建议），Spec 0 项问题；两轴均无阻塞风险。
