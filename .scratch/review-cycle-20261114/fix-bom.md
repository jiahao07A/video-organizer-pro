# BOM 配置备份、恢复与搬迁修复

Status: 修复完成，等待主 Agent 与独立复审

## 修改

- `core/storage_backup.py` 的 `_read_json_object` 改用 `utf-8-sig` 读取配置 JSON。快照仍复制原文件、按原始字节计算大小和 SHA-256；manifest 与恢复日志协议不变。
- `core/data_directory.py` 的 `_validate_copied_layout` 对设置和标签配置使用同样的读取编码。普通 UTF-8 继续可读，JSON 根对象检查与损坏拒绝规则不变。
- 新增 `tests/test_review_bom_storage.py`，9 个场景：普通 UTF-8 / BOM 原字节快照及哈希、修改后恢复真实数据库与配置、BOM settings 恢复后创建新服务读取、管理目录搬迁原字节和迁移前快照保留、设置/标签配置损坏或根数组的负例。

## 验证

修复前，新测试中 BOM 备份/服务恢复/搬迁均因 `Unexpected UTF-8 BOM` 失败。首次合成 DB 夹具漏掉迁移要求的 settings 表，已修正夹具，未改产品约束。

```powershell
rtk proxy python -X utf8 -m pytest tests/test_review_bom_storage.py tests/test_backup_restore.py tests/test_data_directory.py -q -rs --tb=short
```

结果：**50 passed in 19.12s**，无跳过。

## 边界

按主 Agent 决定，未扩展 `VideoOrganizerService.load_tag_config()` 的 BOM 产品承诺；服务重启测试使用 BOM settings 和正常 UTF-8 tag_config。底层备份/搬迁均验证两类配置原字节接受 BOM。未改 service、installer、构建 spec、README，未提交、未删除目录或读取用户配置。
