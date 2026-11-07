# 手动设置文件编辑不被旧配置覆盖

Status: awaiting-user-verification

GitHub Issue: #49
Branch: fix/49-rc1-release

## 背景与范围
遵循 ADR-0007，使活动数据目录内的 settings.json 成为普通启动的用户设置来源。
保留前序修复的未提交修改，不修改用户的真实配置、数据库或素材。
默认值只补充缺失字段；未知自定义字段需要经保存、重启保留。
无效 JSON、非对象根节点不能被当作默认设置静默保存；兼容 Windows 编辑器的 UTF-8 BOM。
供应商档案与任务路由仍是 AI 配置的权威字段，api 中连接身份字段属于兼容投影，在文档中说明。
本次不增加运行中配置文件热重载，不重新发布安装包，不改变路由优先级。

## 验收与任务清单
- [x] 先复现文件与数据库冲突、无效文件静默恢复的问题。
- [x] 普通加载不使用数据库 settings_blob 覆盖文件。
- [x] 添加显式旧数据库恢复入口，普通启动不触发。
- [x] 有效手工修改、未知字段、供应商和路由经保存重载后保留。
- [x] 配置损坏时明确报错并保留原文件，相关测试通过。

## 风险与回滚
已有数据库配置副本不删除，显式恢复仍可使用。用户应关闭程序后编辑活动数据目录中的 settings.json，运行中外部编辑不纳入本轮。
通过源码 diff 回退本次变更；无需迁移数据库或处理生产数据。commit 等待用户验收批准。

## Comments

### 发布授权（2026-11-07）
用户明确要求提交、远端同步和发布 Release，已获得本轮提交与发布授权；与重命名、安装器修复合并到同一候选版本。

### 验证记录（2026-11-07）
- 修复前执行新用例：5 failed，覆盖数据库还原用户编辑、格式错误静默忽略、BOM 文件无法读取。
- 修复后设置持久化、供应商和备份测试：40 passed。
- 扩大验证至设置界面、GUI 回归、工作范围和启动升级：116 passed，耗时 66.02 秒。
- 命令：`python -m pytest tests/test_settings_persist.py tests/test_model_providers.py tests/test_settings_catalog.py tests/test_gui_review_regressions.py tests/test_work_scope.py tests/test_backup_restore.py tests/test_startup_upgrade.py tests/test_startup_upgrade_gui.py -q`。
- `git diff --check` 与修改后 Python 文件编译检查通过。
- README 已说明活动数据目录、退出后编辑、可编辑字段、兼容 API 字段和损坏文件处理。
- 本轮未打包，现有安装包仍为上轮 DLL 修复版本；未访问用户真实设置或数据库。
- 未执行 commit、推送或发布，等待用户验收。

### 按用户要求重新打包（2026-11-07）
- 用户明确要求重新打包后，生成包含 settings 修复的 `0.0.1-rc1` 目录包和安装器。
- 独立验证 Qt6Core、Qt6Gui、Qt6Widgets 动态库加载通过。
- 真实 exe 在临时数据副本中两次打开主窗口并正常退出。
- 在临时配置文件中修改字号、自定义字段，数据库仍保留旧副本；第二次启动触发工作范围恢复保存后，文件修改保留。使用 UTF-8 BOM 文件，保存后改为无 BOM UTF-8，确认真实执行了写回路径。
- 发布目录审计：1084 个文件，无数据库、用户配置、日志、真实视频或冲突 ICU。
- Inno Setup 编译退出码 0。安装位置选择页和覆盖安装 ICU 清理步骤保留。
- 安装包：`release/VideoOrganizer-v0.0.1-rc1-setup-win-x64.exe`，104538758 字节。
- SHA256：`2E614DDBFFA127605358EFDF494C23C5175DEB0A5D85DC13743F8D5C31872B03`；同目录已生成 `.exe.sha256` 校验文件。
- 用户生产目录未参与安装验证，未执行 commit 或外部发布。

### 发布前检查
- 最终合并运行本轮相关 135 项测试：134 passed、1 failed。失败为旧版已记录的首次升级 GUI 等待超时用例，单独复验同样超时；在 PR 与 Release 中明确列为已知限制。
- 用户已授权提交、推送和发布；安装包校验值与上述构建记录一致。
