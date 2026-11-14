# Issue #73 修复验收记录

日期：2026-11-13

分支：`fix/73-invalid-analysis-results`；基线：`61f8ee8`。

## 已确认的原因

原实现只验证返回值能够解析为 JSON，未验证是否完成有效视觉分析。模型返回“抽帧失败，未能获取视频画面内容”时，该文字被保存为摘要、缓存并用于重命名。现场记录显示本地抽帧完成，不能由这段模型输出推断本地解码失败；诊断包未包含原始响应，供应商为什么未完成视觉分析仍无法确定。

## 修复行为

- 新响应、持久缓存、恢复上下文及成功落库使用同一结果契约，复用 Pydantic 严格校验；明确失败标志、失败性摘要及畸形字段进入失败流程。
- 在网关成功记账之前校验，沿用现有有界重试和恢复预算；异常归为响应问题，固定错误说明不包含原始响应。
- 精确移除单条无效缓存；强制重新分析失败保留旧有效成果，阻止标签、XMP 等成功副作用。
- 重命名、预览和监控识别旧污染结果。失败项独立报告；正常项继续；监控进入待核对。仅原名/日期规则仍可使用，人工修正后可再次预览。
- 拦截空文件名，避免空摘要生成仅扩展名；空目标调用不复用上一次失败报告。
- 预览提供简短中文拦截状态，完整原因置于悬停提示，阻止项不可勾选；已检查浅色与深色可读性。

## 验证证据

1. 首个回归在旧实现上检出成功数误为 1，修复后通过。
2. 全仓库：`python -X utf8 -m pytest -q --junitxml=.scratch/invalid-analysis-results/full-tests.xml`，839 passed、9 skipped、25 warnings，527 秒。该轮开始于最后的空文件名、空报告及失败项改名保护之前；最后改动另有最终相关回归覆盖，不将此轮冒充最终状态全套。
3. 最终新增防御测试：68 passed，104 秒，见 `defensive-tests.xml`。覆盖异常响应、误判反例、动态标签、损坏缓存、重试恢复、取消与预算、日志隐私、失败不落副作用、人工纠正、预览/worker/监控、并发隔离、真实抽帧与临时文件改名、空报告及空文件名。
4. 最终相关回归：171 passed，255.51 秒，无失败或跳过，见 `final-regression.xml`。包含上述 68 项防御测试和 GUI、监控、后续加固、真实工作流、精简分析字段的既有回归。命令为 `python -X utf8 -m pytest tests/test_analysis_result_validation.py tests/test_gui_review_regressions.py tests/test_media_watch_workspace.py tests/test_post_review_hardening.py tests/test_real_workflow_regressions.py tests/test_slim_analysis_fields.py -q --junitxml=.scratch/invalid-analysis-results/final-regression.xml`。最后仅调整预览列默认宽度，并通过最终浅/深色截图复核。
5. Ruff：新校验模块及新增测试通过。`git diff --check` 通过。
6. 原始完整复现：2026-11-13 再次运行 `probe_issue73.py`，退出码 0。正常视频抽取 8 帧并生成正常预览；错误摘要成功数 0、失败数 1、不缓存、预览为空、不发生错误改名；本地解码失败 API 请求数 0。
7. 离屏合成记录截图：`rename-light.png`、`rename-dark.png`；仅在临时目录使用合成视频、数据库和模拟客户端。

## 验收边界

明确失败检测不保证识别全部模型幻觉。本轮未复测供应商真实视觉能力、未自动修订历史数据或恢复已改名的真实文件，也未发布可执行安装包。现有测试跳过项和 Qt 弃用警告不作为本次通过项；没有通过放宽断言或删除测试消除失败。

源码、ADR 和测试已完成；等待用户确认修改完成并批准 commit。既有其他任务的未跟踪文件不纳入本次提交。

## 用户要求的循环审查与最终验证

随后按用户要求进行了 3 轮独立审查，每轮使用两名全新 Subagent 分别检查规格和仓库规范，共 6 名审查者。Subagent 仅审查，主 Agent 复现并执行全部修复。完整记录见 [review-cycles.md](./review-cycles.md)。

- 第 1 轮：规格轴 1 项 P2（改名共享报告并发覆盖），规范轴 1 项 P2 类型边界风险（协议状态字段抢占既有动态组 ID）。已补红测并修复；主 Agent 另补显式扩展名、正则删主体导致仅扩展名的保护。
- 第 2 轮：规格轴 1 项 P2（明确屏幕错误提示描述被误判模型自述失败），规范轴 0。已补中英文正常和失败反例并修复。
- 第 3 轮：规格轴 0、规范轴 0；独立定向测试 6 项通过，无待修复发现。

首轮修复相关回归 194 passed；第二轮修复定向验证 50 passed。最终当前源码完整命令：`python -X utf8 -m pytest -q --tb=short --show-capture=no --junitxml=.scratch/invalid-analysis-results/review-final-full.xml`，结果 **876 passed、9 skipped、25 warnings，753.38 秒**。新增防御测试共 **99 项**，包含在最终通过结果中。9 skipped 为默认未启用的本地压力测试；25 warnings 为已有 Qt 弃用警告。本记录此前的 839/171 项为较早验证，最终状态以 876 项完整回归为准。

最终原始复现脚本退出 0；错误摘要不缓存、不生成改名预览且不实际错误改名，正常视频流程通过，本地解码失败无 API 请求。Ruff 与 `git diff --check` 通过。`review-final-source-sha256.json` 记录最终运行源码快照，验证期间未改动源码。

循环审查和修复完成，仍等待用户验收批准 commit，尚未提交或发布。

## 本地打包

用户已追加“打包”。按 docs/软件更新与恢复.md 采用独立 PyInstaller workpath/distpath 及新的安装器输出目录；输出使用本地 Issue 专属目录，不清理、不覆盖既有构建，不生成签名清单、不发布、不安装至生产目录。构建和隔离冒烟结果后续追加。

## 本地打包结果

构建目录：`.release-build/issue73-20261113/`。PyInstaller 6.19.0 使用 Python 3.13.12，在独立 `work/`、`dist/` 成功完成 onedir 构建；构建日志的分析图包含 Pydantic 2.12.5 模块和 pydantic_core，运行包 `_internal/` 中也包含 `pydantic-2.12.5.dist-info` 与原生扩展。应用目录约 366 MB；普通安装器约 105 MB。版本沿用仓库唯一版本源 `0.0.1-rc8`，没有擅自增加版本号。

- 普通安装器：[VideoOrganizer-v0.0.1-rc8-setup-win-x64.exe](../../.release-build/issue73-20261113/installer/VideoOrganizer-v0.0.1-rc8-setup-win-x64.exe)，104,883,899 bytes，SHA-256 `263ca0525e6aed169a2a35d35bce8f128a8bdf5bb4d8e5318e2da9178ab9917c`。
- 隔离冒烟安装器：[VideoOrganizer-v0.0.1-rc8-isolated-smoke.exe](../../.release-build/issue73-20261113/isolated-installer/VideoOrganizer-v0.0.1-rc8-isolated-smoke.exe)，104,883,838 bytes，SHA-256 `9039fd2f791fd68d965d0dfecec9d7345ab94b1b91d02b2defb766a7c3e3ac12`。该变体使用隔离 AppId、不注册卸载项/快捷方式，只适用于本地隔离测试，不能发布。

隔离测试目录 `.release-build/issue73-20261113/smoke/`：首次安装退出码 0，安装标记版本为 `0.0.1-rc8`；应用写入 `startup.window_ready`，隔离更新会话完成，说明打包程序已启动并通过更新启动核验。冒烟脚本随后向测试窗口发送 `WM_CLOSE`，20 秒内进程没有自行退出，脚本因 `TimeoutExpired` 退出。因此本次**没有完整通过** `tools/smoke_installer_update.py`；已核实窗口关闭、进程仍响应且路径属于上述临时隔离安装目录，然后仅停止了该隔离进程。随后对同一隔离目录第二次运行安装器，退出码 0；合成数据库仍有 1 条测试记录，合成设置标记 `preserve-me` 保持不变。这个后续验证证明覆盖安装数据保留，不掩盖首次冒烟的退出超时。

安装包中未发现 `.env`、设置文件、标签配置或数据库；构建只在新目录写入，无已有构建目录被清理。没有生成更新清单/签名，没有发布、安装至正式目录或提交。本地包可供用户检查；正常关闭超时仍是已知待查项。

机器可读的构建产物摘要见 [package-result.json](../../.release-build/issue73-20261113/package-result.json)。

## 2026-11-13 冒烟退出修复复验

上文保留初次冒烟超时的原始记录。复核确认应用在 Windows 托盘可用时把普通 `WM_CLOSE` 作为隐藏到托盘处理，而不是退出；因此超时由冒烟脚本用错退出语义造成，不是证据显示的应用关机故障。应用新增显式 `--smoke-exit` 自动化入口，等待启动更新检查空闲后复用正式“彻底退出”门禁；烟测以 `--background` 避免更改本机登录自启设置。未完成的业务任务仍会阻止退出并使冒烟失败。

三轮全新 Subagent 规格/规范审查，第二轮发现并由主 Agent 移除未调用的旧 `close_test_window`/`WM_CLOSE` 实现；第三轮复审 0 项待修发现。定向烟测与现有托盘行为测试 `6 passed`；编译检查、Ruff、`git diff --check` 通过。

修复版构建位于 `.release-build/issue73-smokefix-20261113/`，使用 Python 3.13.12、PyInstaller 6.19.0 和独立 work/dist。普通安装器 `.release-build/issue73-smokefix-20261113/installer/VideoOrganizer-v0.0.1-rc8-setup-win-x64.exe`，SHA-256 `2E4AB625F095EBDCAA6364A4888E599FF79959C973F107C638BF44C4EBD2D858`。隔离安装器 `.release-build/issue73-smokefix-20261113/isolated-installer/VideoOrganizer-v0.0.1-rc8-isolated-smoke.exe`，SHA-256 `DE2B177D1D084E1B5D7FF85F5F1886EA76CC71D3B5B3059EB48B0AD024D82A57`，仅用于本地隔离验证，不可发布。

使用隔离安装器完整运行 `tools/smoke_installer_update.py` 通过：首次安装完成，更新核对阶段为 `completed`，通过正式应用路径正常退出、退出码 0；二次覆盖安装退出码 0，合成数据库保留 1 条记录，设置标记 `preserve-me` 保留。安装包内未发现 `.env`、用户设置、标签配置、数据库或真实视频素材。未发布、未安装至正式目录、未提交。

## 最终复打包

用户在复审完成后再次要求打包。本次从当前工作区重新构建，目录为 `.release-build/issue73-final-20261113/`，使用 Python 3.13.12、PyInstaller 6.19.0 和新的 work/dist 路径；普通与隔离 Inno Setup 安装器均成功生成，版本仍为 `0.0.1-rc8`。

- 普通安装器：`.release-build/issue73-final-20261113/installer/VideoOrganizer-v0.0.1-rc8-setup-win-x64.exe`，SHA-256 `8090071ADCD7F82D683B8DFDF9F330A2D794CBB25A786EB15267E5F36B72CBC0`。
- 隔离冒烟安装器：`.release-build/issue73-final-20261113/isolated-installer/VideoOrganizer-v0.0.1-rc8-isolated-smoke.exe`，SHA-256 `6E17B12CA2F5AE051A9B0D29602E49908A5CE0524A9B0D0568C2AD1A98A1D491`。该包仅用于本地隔离测试，禁止发布。

对本轮隔离安装器再次完整运行 `tools/smoke_installer_update.py`：首次安装成功，启动更新核对 `completed`，正式退出码 0；二次覆盖安装后合成记录 1 条、设置标记保留。递归检查 onedir 未发现 `.env`、`settings.json`、`tag_config.json`、SQLite/数据库文件或视频素材。没有生成签名更新清单，没有发布或安装至正式目录。

## RC9 发布验证

RC9 已通过 PR #76 以 Rebase and merge 合入 `main`，合并提交为 `ed2744b407a9d9dccf74d119a886effedd31957d`；GitHub Release [v0.0.1-rc9](https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.1-rc9) 于 2026-11-14 发布为预发布版本，标签指向该合并提交。

最终 RC9 隔离安装冒烟首次因系统中已有 Video Organizer Pro 进程而被安装器拒绝，退出码为 1；未结束该进程。用户正常退出旧实例后，在全新 `.release-build/rc9-release-20261114/smoke-retry-20261114/` 目录完整复测通过：安装版本 `0.0.1-rc9`，启动更新会话 `completed`，应用退出码 0，二次覆盖安装成功，合成数据库记录数为 1，设置标记 `preserve-me` 保留。

使用仓库外 Ed25519 私钥生成 `update.json` 和 `update.json.sig`。从 Draft Release 下载全部四项资产后，以客户端验签和安装包验证逻辑检查通过；安装包 SHA-256 为 `d70328db2addcaa2842a887f46441a47dcd4e5723579945fd4ddae5de784f292`。公开 Release 中资产摘要与本地结果一致。该清单签名不代表 Windows Authenticode 签名。

## RC9 提交前回归

将唯一版本源更新为 `0.0.1-rc9` 后，重新运行 `python -X utf8 -m pytest -q --tb=short --show-capture=no --junitxml=.scratch/invalid-analysis-results/release-tests.xml`，结果 **881 passed、9 skipped、25 warnings**，503.98 秒。9 项跳过为需显式开启的本地压力测试；25 项为 `gui/models/tag_model.py` 的既有 Qt 弃用警告。`ruff check core/analysis_result.py tests/test_analysis_result_validation.py tests/test_smoke_exit.py`、`python -X utf8 -m compileall -q core gui tests video_organizer_app.py tools/smoke_installer_update.py` 及 `git diff --cached --check` 通过。全量 Ruff 扫描旧模块另报 28 项历史 lint 问题，不纳入本次修改；新增校验模块与测试 lint 清洁。
