# P1 元数据注入临时文件所有权修复

Status: 修复和定向验证完成；产品方法已冻结释放，待独立复审

## 范围与根因

主 Agent 与存储审查者通过真实 ffmpeg 音视频探针确认，原 `MetadataInjector.inject_tags()` 使用 `video_path + '.tmp.mp4'` 和 `ffmpeg -y`，会覆盖用户预先存在的邻接文件，再把它替换到源视频而移走。此问题为已实测的数据破坏边界，不是推测。

仅修改 `core/video_organizer_service.py::MetadataInjector.inject_tags()` 方法，并新增 `tests/test_review_metadata_injector.py`，没有改其他产品函数。

- 标准库 `tempfile.mkstemp()` 在源视频父目录创建独占 `.video-organizer-metadata-*.mp4` 文件，立即关闭返回描述符，确保Windows上ffmpeg能写入本次拥有的文件。
- 原 comment 拼接、`-codec copy`、隐藏进程参数及 ffmpeg 音视频映射完全保留；不扩展到多流策略或编码变更。
- 输出成功后仍用 `os.replace()` 替换源视频。finally仅移除本次创建的单个临时文件，替换成功后文件已不存在可忽略；清理故障记录日志。不尝试查找或删除旧固定 `.tmp.mp4` 文件。
- ffmpeg或替换失败返回False，保留源视频和任何邻接文件，清理自己拥有的部分输出。

## 验证

新增5项回归先在未修复方法运行，**5 failed in 0.69s**；真实ffmpeg场景明确在旁文件读取处触发FileNotFoundError，中文comment及音视频流在此前检查已正确，锁定实际破坏问题。

修复后：

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_metadata_injector.py tests/test_audio_transcriber.py tests/test_real_workflow_regressions.py tests/test_post_review_hardening.py -q -rs
```

**41 passed in 38.01s**，没有跳过。本机ffmpeg/ffprobe真实执行成功。

新增场景覆盖：

- 预存邻接 `.tmp.mp4` 字节保留，独占输出位于源视频同目录且最终清理；
- ffmpeg失败、原视频被占用导致替换失败：原视频与旁文件保留，只有自己的临时输出被清理；
- 两个不同源并发，两个真实mkstemp路径不同且各自清理；
- 真实ffmpeg生成1秒视频+音轨，注入完整中文comment（含中文标签及&）、ffprobe核对两个流、ffmpeg对全部输出流解码成功、旁sentinel字节保留。

```powershell
rtk proxy python -X utf8 -m py_compile core/video_organizer_service.py tests/test_review_metadata_injector.py
rtk git diff --check
```

均通过。所有测试仅临时合成素材，无用户视频、凭据、注册表或公网请求；未commit。

## 限制

不新增原视频备份承诺，不更改容器/流选择策略。文件系统本身拒绝清理独占临时输出时记录日志并保留该输出；不通过删除其他路径强行清理。Windows10实机、异常断电和跨平台容器支持不在本次已验证范围。
