# runtime-bugs — 本轮 triage总览

Created: 2026-07-20  
Updated: 2026-07-20（实现完成，待用户验收）

| ID | 标题 | Category | Status | 一句话 |
| --- | --- | --- | --- | --- |
| [01](./issues/01-detail-panel-check-spelling.md) | 选中视频详情崩溃 `check_spelling` | bug | **resolved**（待验收） | service 门面 → TagProcessor |
| [02](./issues/02-tags-library-black-ui.md) | 标签库几乎全黑 + Slot 失效 | bug | **resolved**（待验收） | 主题色板 + `@Slot` |
| [03](./issues/03-settings-prompt-templates.md) | 设置 Prompt 不完整/不生效 | bug | **resolved**（待验收） | 方案 B：编辑真实 system_prompt |

## 验收建议

1. 重启应用：`python video_organizer_pyside6.py`
2. 工作台选中视频 → 详情正常，无 `check_spelling` 报错
3. 侧边栏打开标签库 → light 主题下文字/芯片可读；终端无 `load_data_silent` 报错
4. 系统设置 → Prompt 模板 → 应看到「全局系统提示词」；保存后 `tag_config.json` 的 `global_settings.system_prompt` 更新

## 未提交

按仓库约定：用户确认无 bug 后再 commit。