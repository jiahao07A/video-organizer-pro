# 统一视频整理工具 GUI 设计方案

## 1. GUI 布局 (Mockup Logic)
主窗口使用 `ttk.Notebook` 提供三个主要标签页：

### A. 工作台 (Workbench)
- **顶部**：文件/文件夹选择区域（输入框 + 浏览按钮）。
- **中部**：任务表格 (`ttk.Treeview`)，列出视频名、分类、主要标签、内容摘要、以及模拟重命名的预览。
- **底部**：
  - 进度条 (`ttk.Progressbar`) 和 状态文本（如 "正在分析: 视频A.mp4"）。
  - 控制按钮组：[开始分析] [模拟重命名] [应用重命名] [撤销操作]。
  - 实时日志区：可滚动的文本框，显示详细运行日志。

### B. 配置设置 (Core Settings)
- **API 设置**：Key, Base URL, 模型名称。
- **分析设置**：抽帧数、目标尺寸、JPEG质量、最大并发数。
- **命名模板**：输入框允许定义重命名模式，如 `{category}-{tags}-{original_name}`。

### C. 标签库管理 (Tag Manager)
- **分类列表**：列表框 + 添加/删除按钮，管理视频主分类。
- **维度编辑**：针对 Mood, Subject, Location, Action 等维度进行标签词库的增删。

## 2. 后端重构计划 (Refactoring)
- **解耦**：将 `VideoOrganizer` 逻辑提取为独立类，不再直接访问 `sys.argv`。
- **持久化**：引入 `settings.json` 取代硬编码的 `CATEGORIES` 和 `TAG_DIMENSIONS`。
- **回调接口**：为 `VideoProcessor` 和 `VideoOrganizer` 增加 `on_log` 和 `on_progress` 回调，支持 GUI 实时反馈。

## 3. 异步实现方案 (Async)
- 使用 `threading` 模块将耗时的 AI 分析和文件操作放入后台线程。
- 主线程通过 `queue.Queue` 或直接回调方式接收 UI 更新指令。
- 使用 `tkinter.after` 或 `lock` 机制确保线程安全的 UI 更新。

## 4. 实施路线图 (Implementation Plan)
1. **重构基础层**：提取配置到 JSON，解耦控制器类。
2. **构建 GUI 骨架**：创建 Notebook 标签页和基础布局。
3. **实现设置同步**：让 GUI 设置页能读写 `settings.json`。
4. **集成工作流**：将后台线程与 GUI 按钮绑定，实现进度条和日志联动。
5. **完善表格交互**：支持模拟重命名的实时预览和撤销功能。
