# 真实音频转写与 MP4 元数据限定验证

结论：真实音频前处理与 SDK multipart 传输通过；元数据写入确认一项 P1 文件丢失问题，尚未修复。本轮只创建 scratch 探针和合成媒体，未改产品代码、已有测试、未提交。

## 任务清单

- [x] 用真实 FFmpeg 生成 1 秒视频加 sine 音轨。
- [x] ThreadingHTTPServer 接真实 OpenAI SDK 的 audio/transcriptions multipart。
- [x] 校验 whisper-1、音频实际解码、返回文本和源文件保护。
- [x] 真实 MetadataInjector 注入中文摘要与完整标签，ffprobe 和解码校验。
- [x] 预存邻接 .tmp.mp4 sentinel，确认固定路径冲突导致数据丢失，保留红证据。
- [x] 复跑现有音频前处理六项测试。
- [ ] 主代理决定并分派元数据临时路径修复。

## P1：元数据写入覆盖并移走已有邻接文件

位置：`core/video_organizer_service.py:1740` 至 1747，调用入口在同文件 4730 行的 `run_inject_metadata`。

复现条件：对有效视频 `元数据验证视频.mp4` 执行注入，同时目录内已经有不属于本次操作的 `元数据验证视频.mp4.tmp.mp4`。

原因：固定 `temp_path = video_path + '.tmp.mp4'`，执行 FFmpeg 时带 `-y`，先覆盖已有同名文件；随后 `os.replace(temp_path, video_path)` 将该路径移走。现有文件内容不可恢复，而接口返回 True。

真实红证据：

- 注入返回 True，视频 comment 完全等于 `中文摘要：蓝色画面与正弦音轨，验证安全写入。 | Tags: 平静,人物与城市,测试 & 验证,中文标签`。
- 视频 MPEG-4 和音频 AAC 流均保留；ffprobe 读取与 FFmpeg 全流解码成功。
- 注入前邻接 sentinel SHA256：`64fd3248a4e2fddaabe67723031c44dceaa26b0ec94d5931aed4d6acbfa39bcb`。
- 注入后 `neighbor_sentinel_exists_after=false`、`neighbor_sentinel_preserved=false`。
- 探针最后在该保护断言 exit=1；并非以“注入报错”代替数据保护验证。

证据目录：`.scratch/review-cycle-20261114/real-media-9d35310eff164aadb07154e42cddd02d/`；包含合成原视频、注入后目标视频、捕获的请求 MP3 和 `results.json`。本轮未删除该目录、文件或修改产品。

## 真实音频链路结果

- 使用 `AudioTranscriber.transcribe(..., gateway=AiRequestGateway(...))` 原方法，未替换 FFmpeg、subprocess 或 SDK。
- SDK 向仅绑定 `127.0.0.1` 的本地服务器实际 POST `/v1/audio/transcriptions`，请求一次，响应 HTTP 200。
- 使用无真实权限的本地测试 key，连接完全指向本地，没有访问公网。
- multipart 的 `model=whisper-1`、`response_format=text`、文件名 `audio.mp3`，实际文件 9360 字节。
- 捕获音频经 ffprobe 为 MP3、16000Hz、单声道、时长 1.021688 秒；FFmpeg `-xerror` 实际解码成功。
- 返回内容完全等于本地服务器的中文 text。
- 转写前后源 MP4 SHA256 不变，已有 `源视频.mp4.mp3` sentinel 内容不变，产品独占临时目录清理成功。
- 临时路径由探针设置到本轮唯一 scratch 目录的 owned-temp 下，不使用真实素材目录。

## 命令、环境与边界

- `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_real_media.py`
  - exit=1：音频和真实元数据功能验证通过，邻接文件保护断言失败。
- `rtk proxy python -X utf8 -m pytest tests/test_audio_transcriber.py -q`
  - 6 passed in 0.54s；这些 fake 边界测试补充验证了异常和并发所有权，不能代替本轮真实音频链路。
- Python 3.13.12，Windows 11 10.0.26200，OpenAI SDK 2.20.0，httpx 0.28.1。
- FFmpeg/ffprobe `N-123829-g69fc910777-20260404`。
- 本地服务器返回固定文本，本轮验证的是音频提取、SDK 序列化/传输和解析，不声称远端语音识别模型的识别质量。
- 无 Windows 10 实机、可见 GUI、冻结包结论；未进行全量测试。
