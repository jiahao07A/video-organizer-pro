# 06 — 标签库：左待审面板 + 右组栏

**What to build:** 重构标签库主界面：左侧固定**待审面板**（列表 + 批准到组 / 挂别名 / 丢弃 / AI 建议且须确认）；右侧仅各**标签组**栏，支持标准词**标签移动**（拖拽与「移动到」），无中转池栏。**标签使用统计**（原热力图）默认不占主界面，可从顶栏「更多」等次要入口打开；近义巡检、冷启动/加载词表、Prompt/组规则等可达但不抢主视觉。日常组内增删改名与别名管理仍可用。依赖 05 的组归属与无 pool 规则。

**Blocked by:** 05 — 废除中转池 + 组归属规则

**Status:** resolved

**Parent:** [spec.md](../spec.md)

## Acceptance criteria

- [x] 主布局：左待审 + 右标签组栏；无中转池栏
- [x] 待审：批准须选目标组，成功后出现在该组；挂别名 / 丢弃 / AI 建议确认后写库，未确认不写
- [x] 待审与标准词视觉可区分
- [x] 标准词可跨组拖拽或菜单移动；移动只改组不改词面
- [x] 热力图/使用统计默认不常驻主界面；次要入口可打开
- [x] 近义巡检等运营入口在「更多」或等价次要位置
- [x] 删除标签组 UI 走整组改派（与 05 规则一致）；唯一组不可删有提示
- [x] 导入向导无「进中转池」；须指定目标组

## Comments

### 2026-07-21 to-tickets

- 用户批准 7 票发布；依赖 05

### 2026-07-21 implement

- 布局：`TagsView` 主区为横向 body_splitter：左 `PendingTagsPanel`（琥珀边框待审区）+ 右仅标签组栏；无 pool 列、热力图不常驻
- 顶栏：「更多」含标签使用统计 / 近义巡检 / 加载词表 / Prompt 配置 / 管理标签组；导入与保存保留主按钮
- 待审：批准用组下拉（禁 pool）；成功后 `on_pending_resolved` → load_data + silent save 刷新右侧组栏
- 05 已有：editors 整组改派、import_wizard 拒绝 pool；本票未改服务层 API
- 验证：`pytest tests/test_tag_group_ops.py tests/test_tag_normalize.py -q` → 23 passed；`import tags_library` OK