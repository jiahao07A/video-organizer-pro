# PR #5 三项评审问题的修复范围与方案

GitHub Issue: #4
PR: #5
Branch: refactor/ai-request-reliability
起始提交：bf9be1680a22c1006f5eba37748b02d77b169f18
Status: claimed

## 背景与目标

用户要求继续处理 PR #5，延续“审核无问题后合并”的目标。上一轮已通过假客户端与临时文件复现三项新增回归：转录前处理失败删除既有音频、鉴权错误被文本判定覆盖而重试、配置切换时混用不同路由的模型与连接。完整证据见 pr-review.md；原边界复现为 4 failed、主干对照 3 passed。诊断及因果判断已完成，本轮直接进入逐项失败测试与修复，不重复未验证的假设枚举。

## 范围内与范围外

范围内：core/video_organizer_service.py 的 AudioTranscriber；core/ai_gateway.py 的请求路由和失败分类；core/analysis_job_policy.py 的不可恢复错误优先级；对应正式测试和 AI 请求说明；本地规格与评审记录。

范围外：新增路由槽或供应商类型、GUI 行为、依赖升级、真实 API/素材、数据库迁移、标签人工确认边界、非 AI 文件流程重构。初始工作区的 spec.md 修改和两个未跟踪评审文件均属于同一 PR 评审，予以保留；不覆盖或清理用户其他数据。

## 已决定的方案与执行顺序

1. 音频提取使用 tempfile.TemporaryDirectory 创建本次独占目录，输出 audio.mp3 到该目录，退出时只清理拥有的临时资源，保留原有 video_path + .mp3。测试 seam 为 AudioTranscriber.transcribe，外部 ffmpeg 和请求网关采用 fake，检查返回值、既有文件内容和临时输出清理。先验证 ffmpeg 不可用的失败测试，再实施；覆盖成功、抽取中途失败和转录失败。
2. 网关聊天、音频均仅使用 is_retriable(error) 的一次判定，禁止再用字符串推翻明确不可恢复结果。纯策略优先拒绝文件不可用异常，明确 HTTP 4xx（429 除外）不可重试，取消/鉴权/文件文本标记不能被 timeout 抵消；保留瞬时错误和 JSON 解析错误现有次数上限，以及 response_format 兼容降级。测试 seam 为 request_chat、transcribe_audio 和 is_retriable；先观察错误请求次数和失败标记的失败测试，再实施。
3. request_chat 未注入 client 时从同一次 client_for_task 返回值取得 client 和 route，再从 route 取得 model；显式 fake client 时只解析一次。音频去掉多余路由解析，不改变 whisper-1。测试 seam 为 request_chat，通过 fake OpenAI 构造器模拟连接获取期间切换路由，检查发送的供应商/模型对自洽，以及后续请求采用新路由；补充进行中请求保留旧客户端的确定性并发测试。
4. 每个修复形成独立验证与 commit 检查点，不重写已共享历史。完成后主审运行原复现和正式回归；规范与规格两项只读独立复审，禁止修改或代替主审决策。随后记录修复 commit、回复并解决 PR 行内意见。

## 验收与验证

- 既有音频在 ffmpeg 不可用、抽取失败、转录成功/失败时始终保持原内容；本次生成的临时音频退出后清理。
- 鉴权 401/403 和其他不可恢复 4xx 即使消息含 timeout/rate limit/invalid JSON，也只尝试一次；音频文件不可用不进入调用层重试；429/502/503、超时、空响应仍受默认三次上限。
- 路由切换后的请求只能使用完整旧快照或完整新快照，禁止 provider-b/model-a；进行中请求不被 reload 中断，新请求使用新配置。
- py -m pytest -q tests/test_audio_transcriber.py tests/test_ai_gateway.py tests/test_analysis_job_policy.py
- py -m pytest -q .scratch/ai-request-reliability/pr_review_repro_test.py
- py -m compileall -q core gui tests；git diff --check。
- 完整 pytest 重试；若仍缺少 PySide6，明确记录收集限制，并运行排除六个不可收集 GUI 模块的全部现有测试，不能声称完整 GUI/真实服务验证通过。

## 风险与回滚

不涉及迁移、真实素材或配置文件。临时音频移动到系统临时目录，防止与源素材名称冲突；重试分类收紧可能更早报告不可恢复错误；路由快照只改变一致性，不新增缓存方案。各修复 commit 可单独 revert；若验证或复审仍有阻塞，不合并。合并只能通过 GitHub PR，采用默认 rebase，并固定已验证 HEAD 防止审核后提交变化。
