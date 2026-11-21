# 独立复核：接口与基准可信度

Status: claimed

桌面代理完成自己的二轮修改后，独立审查 platform 的 core/ai_gateway.py、gui/views/settings.py 中连接测试改动、两个 benchmark 和相关用例。核查视觉、音频、节流/取消、设置选中供应商的真实 SDK 回环，以及 benchmark 的过滤成功/超时/原始行数和隔离。对照 docs/ai-request-architecture.md、docs/settings-center.md 和 tests/README.md。只读，写证据和任何未清问题到本文件，禁止提交。

## 独立复核记录

- Windows 11：接口、连接/模型、计时、密钥重载、GUI 基准定向 15 项通过（含真实 SDK 回环、合成媒体、音频派发和取消）。1,250 条真实临时库在 GUI 测得首屏 100、筛选 250，SQLite 总数 1,250。
- 实际注入过滤失效：仅在测试进程以 `patch.object(MaterialLibraryView, "_query_params", new=lambda self: {})` 模拟界面丢失搜索条件。基准仍成功返回 `filtered_rows=1250`。建议根据合成输入核对命中数，再报告耗时；已发回主审。
- 第三轮独立复跑相同变异，现以退出码 1、`RuntimeError: 检索结果不符：期望 250 条，实际总数 1250，模型首屏 100 条` 拒绝误报。基准与计时 10 个测试通过。接口、连接、计时和基准本轮无新的确定行为缺陷；外部供应商仍没有进行真实付费调用。

### 作者第三轮修复（待独立复跑）

- 按生成序号范围精确计算检索命中数，并核对实际列表模型的行数和每条文件名。对空结果也核对模型为空，不额外读取全库。
- 新增 `test_ignored_search_condition_is_not_reported_as_a_success`，修复前真实变异复现为 DID NOT RAISE，修复后该变异被拒绝。基准三项回归全通过。
- 真实运行 50 / 1,250 / 50,000 条分别得到首屏 50 / 100 / 100、检索 0 / 250 / 1,000，检索交付约 193 / 211 / 226 毫秒（单次样本，非性能承诺）。
