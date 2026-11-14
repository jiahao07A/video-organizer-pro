# 元数据注入邻接文件所有权独立复审 R2

结论：修复通过，原 P1 的真实红探针转绿；本次定向复审未发现确认回归。只读产品和已有测试，未提交 Git。

## 任务清单

- [x] 核对 `MetadataInjector.inject_tags` 实际修复和新增测试。
- [x] 不修改原 `probe_real_media.py`，重跑旧问题的精准保护断言。
- [x] 用真实 Windows CreateFileW 不共享删除占用原视频，验证替换失败时的数据保护。
- [x] 释放占用后对同一视频重试，核对中文 comment 和双流解码。
- [x] 验证 mkstemp、真实 FFmpeg、replace 和 cleanup 失败边界。
- [x] 新测试、音频前处理和真实工作流组合回归完成。

## 实现核对

- `mkstemp` 在源视频父目录独占创建 `.video-organizer-metadata-*.mp4`，随即关闭文件描述符，实际 FFmpeg 能正常打开输出。
- 原 FFmpeg 输入、metadata、`-codec copy` 和隐藏窗口参数保持。
- 成功只用 `os.replace` 将本次独占产物替换源文件；代码不再构造、写入或清理旧的 `video_path + '.tmp.mp4'`。
- finally 只处理本次得到的 `temp_path`，`FileNotFoundError` 作为已替换的正常状态，其他 OSError 记录 `media.metadata_temp_cleanup_failed`。
- 新测试共4个测试函数、参数展开5项，覆盖成功保护、FFmpeg/replace 两类失败、并发不同素材独占路径和真实 FFmpeg 中文音视频。

## 独立真实验证

1. 原未修改脚本：

   `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_real_media.py`

   exit=0。音频仍使用真实 FFmpeg、真实 SDK 和本地 multipart 接口。元数据注入后的 `neighbor_sentinel_exists_after=true`、`neighbor_sentinel_preserved=true`；完整中文 comment、MPEG-4/AAC 双流和全流解码通过。

   新绿证据：`.scratch/review-cycle-20261114/real-media-a15bb7960edc4f4d88fac0ba6b281e48/results.json`。

2. 独立 Windows 与失败边界脚本：

   `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_metadata_r2.py`

   exit=0，五项边界全部通过：

   - **真实 replace 受阻**：CreateFileW `GENERIC_READ` + `FILE_SHARE_READ | FILE_SHARE_WRITE`，不授予 FILE_SHARE_DELETE。注入返回 False，原视频 SHA256 和邻接 sentinel 不变，自有 temp 清理完毕。
   - **同文件重试**：CloseHandle 后再次注入返回 True，摘要及全部中文/特殊字符标签精确一致，视频和音频两类流均保留并用 FFmpeg `-xerror` 实际解码，邻接文件仍不变，自有 temp 为零。
   - **mkstemp 失败**：定点注入 PermissionError，返回 False，无自有 temp，源视频和邻接文件不变。
   - **真实 FFmpeg 失败**：以隔离的非视频输入触发真实 FFmpeg 错误，返回 False，输入 SHA256 和邻接文件不变，自有 temp 清理完毕。
   - **cleanup 失败**：相同真实 FFmpeg 失败后，定点拒绝 remove，返回 False，源及邻接文件不变，记录明确日志，仅保留本次独占空产物作现场证据。没有改动或误报源文件为已成功写入。

   新证据：`.scratch/review-cycle-20261114/metadata-r2-6e87696f8ed14b00a976f870ac85f9cf/results.json`。

## 回归结果

- `rtk proxy python -X utf8 -m pytest tests/test_review_metadata_injector.py tests/test_audio_transcriber.py tests/test_real_workflow_regressions.py -q`
  - 21 passed in 17.03s。
- `rtk git diff --check`
  - exit=0。

## 验证边界

- 环境为本机 Windows 11，真实文件共享权限、FFmpeg、ffprobe；不声称新增 Windows 10 实机、可见 GUI 或冻结包验证。
- 故障注入仅针对 mkstemp 拒绝与 cleanup 拒绝；replace 受阻和 FFmpeg 解码失败为真实执行路径。
- 所有素材与失败产物只在唯一 scratch 子目录，保留现场，没有批量删除。
