# 02：一次性升级迁移与启动门禁

Status: claimed

GitHub Issue: [#9](https://github.com/jiahao07A/video-organizer-pro/issues/9)

Parent: [#7](https://github.com/jiahao07A/video-organizer-pro/issues/7)

Blocked by: 01（[#8](https://github.com/jiahao07A/video-organizer-pro/issues/8)，可靠备份与受控恢复）

Spec: [父规格](../spec.md)
Branch: `feat/9-gated-one-time-upgrade`
PR: [#30](https://github.com/jiahao07A/video-organizer-pro/pull/30)

## 要交付的行为

首次升级经确认后显示进度并安全完成，相关数据操作在完成前暂停；重启直接使用已升级数据，不再自动回灌旧结果 JSON。先沿用当前活动路径，统一目录由后续任务接入。

## 范围内

启动协调入口、轻量版本检查、版本化升级、旧 JSON 首次导入确认、快照/转换/验证/激活/完成记录、进度与门禁、来源报告、失败和重启恢复。

## 验收标准

- [x] 有效数据库存在时不自动补充/覆盖旧 JSON；编辑与取消入库结果重启不变。
- [x] 无旧数据库、仅有旧 JSON 时经确认批量导入，使用有界内存/事务，重复与无效行有脱敏报告。
- [x] 损坏数据库明确报错而非创建空库；不支持的新版本拒绝写入。
- [x] 确认前不写主数据；执行中窗口可响应、显示进度并暂停相关操作。GUI 通过启动前模态确认对话框和后台线程完成门禁与进度展示。
- [x] 备份及验证成功后才启用和记完成；中断/失败保留原数据并提供恢复/重试。
- [x] 完成后普通启动只查轻量状态，不读全量旧 JSON、不重放数据/结构升级；已有入库编号不重新分配。

## 验证计划

在临时目录通过启动协调入口覆盖新库、仅旧库、仅 JSON、共存、损坏、重复路径与未知版本；注入备份/转换/验证/激活/完成记录失败并重启验证。GUI 走查确认、进度、取消确认和操作门禁。

## 约束、风险与回滚

复用 01 快照协议；保留源与失败记录，不用删除旧文件规避重复导入。数据版本提升需说明旧代码兼容边界，代码回退不是数据回退。遵守 ADR-0007、ADR-0008并更新升级说明。

范围外：静默合并旧 JSON、换数据库、原始视频移动、统一目录 UI、全量日常读写重构。

## Comments

### 2026-10-11 任务发布

用户确认依赖 01。本次未实施，须等待前置任务验收并集成后再从最新主干创建独立实现分支。

### 2026-10-18 核心服务切片实现

- 在 `core/startup_upgrade.py` 增加启动升级协调器：轻量检查数据库完整性/版本，区分无需迁移、需要确认、已完成、取消和失败；损坏库与未知版本不降级为空库。
- `FileManager` 构造不再读取或回灌旧结果 JSON。仅有旧 JSON 时，服务层先抛出 `StartupUpgradeError` 提供确认结果；显式确认后才创建新库、复用 `StorageBackupManager` 快照协议、按固定缓冲和有界事务导入，并持久化完成记录。
- 增加启动升级日志，进程中断后阻止打开半完成数据库；再次确认会清理半成品并重试。有效数据库优先，旧 JSON 不会补回已取消入库记录。
- 新增 `tests/test_startup_upgrade.py`，覆盖正常导入、取消、有效数据库优先、损坏 JSON/数据库、未知版本、重复/无效行、流式大行、备份/写入失败、重启恢复与幂等启动。
- 验证：`python -m pytest -q` 为 360 passed；`python -m compileall -q core gui tests` 和 `git diff --check` 通过。GUI 确认/进度门禁仍不在本切片范围内。
- 当前分支：`feat/9-gated-one-time-upgrade`；PR 创建后补充链接。

### 2026-10-18 PR 创建

已推送 commit `4809d22` 并创建 PR [#30](https://github.com/jiahao07A/video-organizer-pro/pull/30)。PR 当前开放审阅；合并前保留 GUI 确认/进度门禁未完成的限制，Issue 不在本切片提前关闭。

### 2026-10-18 阻塞项修复

- 新增 `gui/startup_upgrade_dialog.py`：启动前显示升级预检、确认/取消按钮和导入进度；预检与导入均在 Qt 后台线程执行，主窗口只在升级完成后创建，因此升级期间不会放行素材库操作。
- 普通数据库启动检查改为只读取 SQLite 版本和核心结构；`PRAGMA quick_check` 仅用于首次导入完成后的显式验证与备份校验。
- 旧 JSON 预检和导入改用磁盘临时 SQLite 去重索引；问题报告最多保留 100 条明细，同时保留总数，内存不再随记录数保存路径集合或完整报告。
- 确认后先复制并校验源 JSON 临时快照，后续只从摘要一致的快照导入，避免确认后的源文件变更造成导入内容和完成记录不一致。
- 新增 3 项核心回归和 1 项 GUI 回归，覆盖报告上限、轻量检查、源文件变更拒绝、GUI 确认与进度。
- 验证：`python -m pytest -q` 为 `364 passed`；`python -m compileall -q core gui tests video_organizer_app.py` 通过；`git diff --check` 通过。
