# FCPXML 结构、转义与 URI 修复

Status: 修复完成，等待独立复审

## 已按执行包修改

- 仅修改 `core/video_organizer_service.py` 导入及 `export_to_fcpx_xml()`，使用标准库 ElementTree 构造 XML，避免名称、分类、标签和摘要中的 `&`、引号及尖括号破坏文档。
- 由原先声称 1.8、但资源层级不合法的输出，修复为有本轮官方 DTD 证据的 **FCPXML 1.7**：`resources/asset` 保存源媒体，`library/event/asset-clip` 通过合法字母前缀 ID 引用资源，摘要为 note，类别和逐个标签为 keyword，event 下生成去重 keyword-collection。
- 源文件 URI 使用 `Path(os.path.abspath(path)).as_uri()` 编码中文、空格、`#`、`%` 等字符，未添加探测、转码或媒体时长猜测。
- 保留 `selected_paths=None` 全库、`[]` 空集合、规范化路径裁剪、副本过滤、空集合返回 False、成功返回 None 的既有行为。
- 新增 `tests/test_review_fcpx_export.py` 的三个实际服务/临时数据库测试，检查元数据解析往返、完整标签、合法 ID/引用、URI 解码、集合去重、范围排除和真实重复副本过滤。

## 红绿验证

修复前，三个新测试全部失败：特殊字符无法解析；resources 中没有资产；副本过滤场景同样因旧资源结构不符失败。

本地独立探针 `probe_fcpx_dtd.py` 使用机器上已有 lxml 读取主 Agent 提取的官方 `FCPXMLv1_7.dtd`。运行真实产品导出入口，旧输出版本归一为1.7后，官方 DTD 仍拒绝 `event` 内直接放 asset 及 asset 内放 note/keyword-collection；修复后实际输出版本1.7，DTD校验通过：`official_1_7_dtd_valid=True`。

```powershell
rtk proxy python -X utf8 .scratch/review-cycle-20261114/probe_fcpx_dtd.py
rtk proxy python -X utf8 -m pytest tests/test_review_fcpx_export.py tests/test_post_review_hardening.py tests/test_media_watch_workspace.py -q -rs --tb=short
rtk git diff --check
```

结果：**57 passed in 64.26s**，无跳过；DTD与空白检查通过。

## 验证边界

官方 DTD 源：<https://developer.apple.com/library/archive/documentation/Miscellaneous/Conceptual/LegacyDTDsFinalCutPro/FCPXMLDTDv1.7/FCPXMLDTDv1.7.html>。本轮未得到1.8官方契约，按主 Agent 决定显式修复为1.7；未把lxml加入运行依赖或CI必需依赖。

DTD合法与元数据往返不等于剪辑软件实机导入通过。没有 Final Cut Pro / DaVinci 导入环境，本轮没有宣称对应实机兼容性已验收。未修改 README、回滚/分析等其他函数，未提交、未删除目录或触碰真实素材。service 文件编辑权已释放。
