# 03：统一便携数据目录

Status: claimed

GitHub Issue: [#10](https://github.com/jiahao07A/video-organizer-pro/issues/10)

Parent: [#7](https://github.com/jiahao07A/video-organizer-pro/issues/7)

Blocked by: 02（[#9](https://github.com/jiahao07A/video-organizer-pro/issues/9)，一次性升级迁移）

Spec: [父规格](../spec.md)
Branch: `feat/10-portable-data-directory`
PR: [#32](https://github.com/jiahao07A/video-organizer-pro/pull/32)

## 要交付的行为

默认程序旁 data 目录统一保存管理数据；用户可查看、自选并安全搬迁，启动工作目录不再改变数据位置，原始视频仍保持原路径。

## 范围内

默认/自选路径、启动定位记录、设置展示与不可写提示、旧布局检测、通过升级协议复制/验证/激活、全部管理数据路径接入、缩略图相对引用和整体搬迁。

## 验收标准

- [ ] 任意工作目录和快捷方式启动使用同一选定管理目录；开发/打包路径计算明确。
- [ ] 默认程序旁 data 不可写时提示另选，不静默转移或打开新空库。
- [ ] 自选位置持久保存、设置可见；定位记录不复制业务设置或密钥。
- [ ] 数据库、设置、标签配置、缩略图和备份统一目录边界。
- [ ] 搬迁经确认、备份与验证才激活，源数据保留；目标已有库时区分打开已有目录与搬迁，禁止隐式合并/覆盖。
- [ ] 搬迁后内部缩略图可用，入库编号、分析结果、配置不变；原始视频不移动，缺失不自动取消入库。

## 验证计划

临时库、小型测试缩略图与中文路径，覆盖不同工作目录、默认/自选位置、权限失败、目标已有库、复制/验证/激活失败、开发/打包路径计算及 GUI 设置反馈。

## 约束、风险与回滚

依赖 02 的可恢复激活协议；验证成功前活动位置不变，失败恢复旧路径和缓存，保留源快照。遵守 ADR-0007、ADR-0008，更新目录、权限和搬迁指引。

范围外：多库切换产品、云同步、原始视频移动或跨电脑路径重定位、自动清理源目录。

## Comments

### 2026-10-18 任务启动

#9 已通过 PR [#30](https://github.com/jiahao07A/video-organizer-pro/pull/30) 合入 `main`（合并提交 `b93031d614adb99ab8de07efd2ad830bf3f50a93`），本任务从最新主干创建 `feat/10-portable-data-directory`。本轮先交付核心路径定位、默认/自选目录、显式目标冲突和可恢复搬迁协议；GUI 设置入口与人工 Windows 走查随核心协议一并接入，统一数据目录以外的性能验收不纳入本任务。

### 2026-10-18 实现与验证

已完成并提交 `2916a7a`，创建 PR [#32](https://github.com/jiahao07A/video-organizer-pro/pull/32)。显式传入数据库/结果路径的服务实例使用调用方目录下的设置、标签配置、备份、缩略图和结果路径；无参数默认应用路径仍保留程序旁旧布局门禁。普通目录定位使用轻量数据库版本/核心结构检查，复制验证阶段执行完整性检查。设置页已接入目录查看、显式打开已有目录和受控搬迁入口。

验证：`python -m pytest -q` 为 373 passed；`python -m compileall -q core gui tests video_organizer_app.py video_organizer_pyside6.py` 通过；`git diff --check` 通过。工作区既有未跟踪资料未纳入提交，源旧布局未删除或覆盖。

### 2026-10-18 补充回归

补充独立的旧布局迁移测试，并清理设置页未使用导入，提交 `8629fe4` 已推送到 PR #32。最终全量验证为 `python -m pytest -q`：374 passed；编译检查和 `git diff --check` 仍通过。
