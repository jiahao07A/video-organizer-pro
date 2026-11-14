# R2 FCPXML修复独立复审

Status: 通过；未发现本执行包的新问题
Reviewer: review_storage（首审发现者，没有编写本组导出修复）

## 审查范围与结论

检查 `core/video_organizer_service.py` 的 FCPX 导入和 `export_to_fcpx_xml()` 实际diff、`tests/test_review_fcpx_export.py`、`fix-fcpx.md`、官方1.7 DTD本地副本及 `probe_fcpx_dtd.py`。已知且获主 Agent 决定的1.8至1.7格式修正纳入验收范围。

- ElementTree承担属性和文本转义，名称、摘要、类别和全部标签通过实际XML解析往返，不再直接拼接用户内容。
- `resources/asset` 与 `library/event/asset-clip` 分工符合本轮官方DTD；资源使用字母前缀合法唯一ID，clip引用与资产一一对应，note/keyword位置正确，事件关键词集合去重。
- `Path(...).as_uri()`产生标准file URI，特殊字符不会被错误解释为query/fragment，解码后定位真实原视频。
- None全库、空列表不输出且False、规范化选入路径、真实重复副本过滤均保留。成功返回None与原入口一致。
- 没有新增媒体时长/格式猜测、转码、用户数据写入或第三方运行依赖；官方DTD允许所省略的时长等属性，本轮不据此承诺剪辑软件运行时解析表现。

## 独立运行

1. `rtk proxy python -X utf8 -m pytest tests/test_review_fcpx_export.py tests/test_post_review_hardening.py -q -rs`：**23 passed in 15.82s**，exit=0。
2. `rtk proxy python -X utf8 -m ruff check tests/test_review_fcpx_export.py --select F401,F811,F821`：All checks passed，exit=0。
3. `rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_fcpx_dtd.py`：实际版本1.7，`official_1_7_dtd_valid=True`，exit=0。
4. 新独立探针 `rtk proxy python -X utf8 .scratch/review-cycle-20261114/fcpx_r2_probe.py`：真实OpenCV创建10帧MP4并解码成功，随后改为含中文、空格、&、#、%、单引号的文件名；真实SQLite登记原件与相同内容副本，调用真实全库导出入口；**原始未改写输出**通过本地官方1.7 DTD验证，完整名称/摘要/类别/标签/集合往返、ID引用、URI定位读取真实视频及副本排除全部断言通过，exit=0。

## 边界与限制

官方DTD来源由主 Agent 固定为 Apple 归档文档：<https://developer.apple.com/library/archive/documentation/Miscellaneous/Conceptual/LegacyDTDsFinalCutPro/FCPXMLDTDv1.7/FCPXMLDTDv1.7.html>。本复审使用该本地副本和已有lxml，没有把lxml加入产品或CI依赖。

**DTD合法、元数据完整和URI可定位，不等于Final Cut Pro/DaVinci/Premiere实机导入通过。** 当前Windows11没有Final Cut Pro实机；本轮不承诺其时间范围/媒体推断或跨操作系统路径迁移的实际表现。没有将其他并行修复差异重复视作本组问题。

本轮只新增scratch独立探针与报告，未改产品/测试、未commit、未访问用户素材或凭据、未删除目录。
