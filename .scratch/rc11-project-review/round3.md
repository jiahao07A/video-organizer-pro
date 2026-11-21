# 第三轮：复核退回的结果提示

Status: claimed

- 媒体写回（storage 负责，对应 cross-media.md 的 P2）：启用写入时，FFprobe/FFmpeg 缺失、校验未通过或写入失败必须在用户分析日志明确提示“分析数据保留，未写入源文件”，必要时区分诊断原因；沿用当前业务分析成功语义，不误将视觉成功计为分析失败。更新 README 依赖说明并加回归，验证日志、数据库和文件字节。
- 媒体并发 P1（storage 负责）：见 cross-media.md 末尾及 .release-build/review-rc11-api/cross_concurrent.py。源在内嵌写回期间被第三方替换时拒绝旧分析入库，不自动重复付费请求；在未写入后、upsert 前重新验证素材身份/版本、重复角色和人工修改，保留原有处理普通写入失败的语义。使用真实写入时序验证 succeeded/failed、DB 指纹、实际磁盘和旧摘要不污染；特别区分缺少 FFprobe 的正常输入和第三方修改。
- FCPXML（desktop 负责）：工作台 export_fcpx 的 job 无视服务返回 False。仅有重复副本时确认 False、不写新文件/不覆盖现有哨兵、不提示成功；既有成功返回 None 也要正确处理（或一致地改用明确布尔并核对所有调用方）。保持 GUI 忙碌门禁，真实数据库/界面用例复核。主动 JSON/CSV 导出注意不要覆盖应用定位器/安装标记等内部 JSON；在明确的公用管理路径上保护，避免把普通用户的同名文件误判成管理数据。
- 文件名（desktop 负责）：授权修复 FileManager.sanitize_filename 中 TAB/C0 非法 Windows 控制字符，不能通过改为不包含控制符的测试样本规避这个真实故障；使预览与执行共享合法化，补原始 TAB/C0 的 Windows 实际改名回归。保持原名扩展名、中文、已有业务字段语义。
- 交叉复核按 cross-*.md 继续，作者完成本轮后交给对方实测。不要提交。
- 基准（platform 负责，对应 cross-interface.md）：1250 条样本把检索条件吞掉，基准仍把 1250 条全库当成搜索成功。按生成的文件名精确计算期待命中数，核对模型行内容（含空结果，计时应包含真正结果交付），不引入全库昂贵读入；新增“忘记应用搜索条件”的故障注入回归。完成后让 desktop 复跑变异，写入 cross-interface.md。
- 测试可移植性（platform 负责）：主审全量将临时目录放入中文项目路径，有 5 项 test_scan_service_progress 失败，产物 WebP 实际有效。该文件两处 cv2.imread 无法打开 Windows 中文路径，改为读字节再 cv2.imdecode；保持原断言（真解码、像素尺寸、故障分支）而非 skip。真实复现留在 .release-build/review-rc11-main/suite-temp-a，主审确认 WebP 68/76 bytes 可用 imdecode 得 (38,64,3)。定向用中文 --basetemp 验证全部通过。顺便修 tests 里相同 read 方式，已搜全库只有此两处。

### Windows 中文路径测试修复证据

已将两处图片读取改为 Path.read_bytes / np.frombuffer / cv2.imdecode；原本可解码、最大边长和变化文件不得绑定缩略图的断言全部保留。全 tests 检索只有此两处 cv2.imread。

`rtk proxy python -X utf8 -m pytest -q tests/test_scan_service_progress.py --basetemp=.release-build/review-rc11-platform/中文临时测试-20261121 -rs`：Windows 11，8 passed in 3.39s，无 skip。
