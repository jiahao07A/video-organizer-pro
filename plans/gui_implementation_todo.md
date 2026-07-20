# Unified Video Organizer GUI 实施待办列表 (TODO)

## 第一阶段：后端重构与解耦 (Backend Refactoring)
- [ ] 创建 `settings.json` 模板，包含 API 设置、默认视频参数、`CATEGORIES` 和 `TAG_DIMENSIONS`。
- [ ] 修改 `video_organizer.py`：
    - [ ] 将全局常量替换为从配置加载。
    - [ ] 重构 `VideoOrganizer` 类，使其支持通过构造函数注入配置。
    - [ ] 为所有核心方法增加 `callback(msg, level)` 和 `progress_update(current, total)` 接口。
- [ ] 确保重构后的代码仍支持原有的命令行调用逻辑（通过 `if __name__ == "__main__":` 保持兼容）。

## 第二阶段：GUI 基础架构构建 (GUI Scaffolding)
- [ ] 创建 `video_organizer_gui.py`。
- [ ] 实现主窗口布局：
    - [ ] 使用 `ttk.Notebook` 创建三个标签页：工作台、设置、标签库。
    - [ ] 工作台布局：包含路径输入、浏览按钮、Treeview 结果表格、日志区。
    - [ ] 设置页布局：API 配置表单、视频参数滑块/输入框。
    - [ ] 标签库页布局：双列表框结构，支持分类与维度的实时编辑。
- [ ] 集成 `settings.json` 的读写逻辑到 GUI 界面。

## 第三阶段：异步执行与工作流集成 (Async & Workflow)
- [ ] 实现线程安全的日志重定向：将后端的 print 信息捕获并显示在 GUI 日志区。
- [ ] 实现后台线程：
    - [ ] 编写 `AnalysisThread`：调用后端的分析逻辑并更新进度条。
    - [ ] 编写 `RenameThread`：执行模拟预览和实际重命名。
- [ ] 绑定按钮事件：
    - [ ] "开始分析"：触发 `AnalysisThread`，并将结果填充到 Treeview 表格。
    - [ ] "模拟预览"：生成新文件名预览并更新 Treeview。
    - [ ] "应用重命名"：执行物理文件重命名并记录日志。
    - [ ] "撤销操作"：调用原有的 Undo 逻辑。

## 第四阶段：UI/UX 优化与抛光 (Polishing)
- [ ] 增加表格双击修改功能（允许用户手动修正 AI 识别的标签）。
- [ ] 优化进度条显示。
- [ ] 增加 Windows 原生风格的间距和控件对齐。
- [ ] 完成最终的集成测试。
