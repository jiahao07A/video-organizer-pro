# 独立复核：媒体与数据

Status: claimed

接口首审者（api）只读复核 storage 的改动：容器、音轨、未识别/不支持时原文件是否保持、FFprobe 新依赖与反馈、流元数据/并发修改；哈希优化不越过资格校验、Unicode 工作范围、目录查询空页排序与 20 万行性能、监控线程锁和事务。查看当前 git diff 与 tests/test_rc11_storage_review.py，运行 .release-build/review-rc11-api/measure_hash.py 和适当交叉用例；只返回真实可复现阻断、行号和边界。请直接在本文件末尾追加结果，不碰产品、不要提交。第一轮数据作者仍在收尾时可先复核已落盘区块、等最后改动后补查。

## 复核结果

- 独立执行素材注册、监控、目录、媒体写入四个测试模块：35 项通过。合成 MKV 保持 Matroska 和两条音轨，一次完整正常分析读全文件四次（基线五次）；MP4 的 90° 旋转矩阵写入前后相同。另一进程修改文件的时序用真实临时文件模拟：原件在输出就绪前大小改变，写入拒绝并保留外部修改。
- 20 万条合成 SQLite 记录，首屏 100 条三次 4 / 5 / 6 ms；带子目录工作范围（1000 条匹配）453 / 435 / 469 ms；状态筛选（66,667 条）50 / 55 / 52 ms。Windows 非 ASCII 路径范围、相邻目录排除、空页总数和稳排序回归通过。
- **P2 待修**：没有 ffprobe 时，勾选内嵌元数据的分析仍以成功结束，外观与实际写入成功完全相同。`MetadataInjector._probe` 新增硬依赖；`_persist_current_analysis`（本轮约第 4784 行）仅将失败记入库，返回结果和用户日志均没有说明。合成 MP4 探针实测 `metadata_injected=False, status=analyzed, source_unchanged=True, return=0, ui=[]`；README 只要求安装 ffmpeg。分析结果应保留，文件副作用的失败应单独提示。
- 临时证据：`.release-build/review-rc11-api/{measure_hash,cross_storage,cross_metadata}.py`；未改动产品。
- **P1 待修**：入口单测能保护被外部改动的原文件，但整条链路随后仍把旧画面的结果标为新文件的成功成果。独立探针 `cross_concurrent.py` 在目标临时文件已经 FFprobe 后改写源字节，`_persist_current_analysis` 在注入返回 False 后仍 upsert。实测 `succeeded=1, failed=0, metadata_injected=0, fingerprint_state=verified`，数据库 file_hash 与原件 MD5 不同。需要在源文件变更时拒绝成功提交；与缺少工具时保留成功的情况分开。

## 第三轮独立复验

- 上述 P1、P2 已在当前源码关闭。只运行原有独立探针和作者新增回归，没有修改产品代码。
- 原 `cross_concurrent.py` 在真实双音轨 MKV、真实 SDK 回环请求、真实抽帧/音轨提取和输出就绪后改写源字节的相同触发条件下，现为 `succeeded=0, failed=1, status=failed, summary=null, fingerprint_state=review`，库内 file_hash 与当前文件 MD5 一致。没有因保存失败重新提交 API；请求仍是一次 429 后的视觉成功及一次音频成功。脚本末尾保留的旧 `succeeded == 1` 断言现在失败，正好确认原缺陷已无法复现；作者新增用例以新预期通过。
- 原 `cross_storage.py` 中 ffprobe 调用故障现产生用户日志“分析数据已保留，未写入源文件”，原件字节与分析成功数据均保留。针对 PATH 中实际无工具的新增用例另验证提示中包含 ffprobe；README 已明确转录需要 ffmpeg、内嵌元数据还需要 ffprobe，并说明无法保真写入时的行为。
- 运行 `rtk proxy python -X utf8 -m pytest -q tests/test_rc11_storage_review.py tests/test_review_metadata_injector.py --tb=short --basetemp=.release-build/review-rc11-api/cross-media-third`，结果 **14 passed**。包括正常双轨写入、源变化拒绝提交、工具缺失警告、原件保留、指纹资格、目录 Unicode 查询、分页与监控连接边界。
- 原 20 万行探针本轮首屏 5 / 4 / 4 ms、范围查询 363 / 393 / 357 ms、状态筛选 45 / 48 / 45 ms。当前复验未发现新的阻断。
