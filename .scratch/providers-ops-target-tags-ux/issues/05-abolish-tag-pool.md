# 05 — 废除中转池 + 组归属规则

**What to build:** 废除**中转池**作为标准词落点：标准词必须属于某一标签组。升级时将原 pool/未分组标准词**降为待审词**；已贴在视频上的对应文字**保留**（不硬清素材标注）。删除非空标签组须**整组改派**到另一组后再删；禁止删除唯一剩余标签组。标签导入与批准待审写入标准词时**必须指定目标组**，否则拒绝。服务层规则可单测（S4）；本票可不完成最终左右分栏 UI（见 06）。

**Blocked by:** None — can start immediately

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 升级/迁移：pool 或未分组标准词 → 待审队列；标准词表中不再保留 pool 归属；可单测
- [x] 上述迁移不批量删除视频素材上已有标签字符串
- [x] 删除非空标签组：必须选择目标组并整组标签移动后才删除；空组可直接删
- [x] 唯一剩余标签组不可删除
- [x] 导入写入标准词缺目标组 → 失败/拒绝
- [x] 批准待审为标准词缺目标组 → 失败/拒绝
- [x] API/文案不再把中转池当作合法标准词落点

## Comments

### 2026-07-21 to-tickets

- 用户批准 7 票发布；主接缝 S4；frontier

### 2026-07-21 implement

- 新建 `core/tag_group_ops.py`（纯逻辑 S4）
- `VideoOrganizerService.migrate_pool_standard_tags_to_pending` 启动时执行；`delete_tag_group` / `resolve_pending_tag` 强制 group_id
- 导入 `persist_classified_tags` 拒绝 pool；向导去掉中转池选项
- 标签组编辑器删组改为整组改派；标签库去掉 pool 列与移动目标
- 测试：`tests/test_tag_group_ops.py` + 既有 normalize/import
- UI 全面左右分栏留给 06