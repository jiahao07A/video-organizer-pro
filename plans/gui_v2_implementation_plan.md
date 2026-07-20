# GUI V2 实施计划 (Code Mode)

## 阶段 1: 数据模型与后端增强
1.  **更新 `video_organizer.py`**:
    *   在 `VideoProcessor` 中添加 `save_thumbnail(image_array, output_path)` 方法。
    *   修改 `VideoOrganizer._process_single_video`，在提取帧时保存缩略图。
    *   在 `FileManager` 中增加 `update_item` 和 `delete_item` 逻辑。
    *   更新 JSON 保存逻辑，包含 `status` 和 `manual_override` 字段。

## 阶段 2: 标签管理 UI 升级
1.  **重构 "标签库管理" 标签页**:
    *   移除旧的 `Text` 框。
    *   创建 `Category Listbox` 和 `Tag Listbox`。
    *   实现选择分类显示对应标签的逻辑。
    *   添加“添加分类/标签”、“删除分类/标签”按钮。
    *   添加“加入正式库”按钮，用于处理 `Learned Tags`。

## 阶段 3: 交互式工作台实现
1.  **增强 `Treeview`**:
    *   添加 `Status` 列。
    *   实现排序功能（点击表头）。
    *   添加搜索和过滤栏。
2.  **实现详情面板 (Side Panel)**:
    *   在表格右侧（或下方）创建可折叠面板。
    *   集成 `Pillow` 显示缩略图。
    *   提供分类（Combobox）、摘要（Entry）、标签（Entry）的实时编辑。
    *   添加“保存修改”按钮。

## 阶段 4: 逻辑集成与测试
1.  **多选处理**:
    *   更新按钮回调，使其根据 `tree.selection()` 执行操作。
2.  **手动覆盖保护**:
    *   在 `run_analysis` 中跳过 `manual_override=true` 的条目。
3.  **最终测试**:
    *   验证缩略图生成和加载。
    *   验证标签编辑同步到配置。
    *   验证手动修改后的条目不会被 AI 覆盖。
