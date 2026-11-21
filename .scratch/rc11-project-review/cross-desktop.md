# 独立复核：桌面与更新偏好

Status: claimed

平台代理完成自己的二轮修改后开始，仅审查 API 代理改动的 gui/main_window.py、gui/views/material_library.py、gui/services/update_controller.py 和测试。检查主窗口快备份共用忙碌和落盘门禁、主题在页面未打开与已打开时写入正确目录及错误状态、编辑后的筛选结果和窗口导航。检查损坏更新时间恢复保留有效偏好。用专属临时数据独立复现关键路径，写下结果与仍有证据的缺陷。不要再改产品，禁止提交。

## 独立复核证据

- Windows 11 build 26200 原生窗口、独立 SQLite 与临时 LOCALAPPDATA：`rtk proxy python -X utf8 .release-build/review-rc11-platform/cross_desktop.py` 退出 0。未打开设置页时点侧栏改为浅色，设置文件随即写入绑定的数据目录且未创建设置页；已打开设置页后改回深色，窗口、表单和磁盘一致；注入写入错误后，窗口不换色，保留旧磁盘值并显示“主题未应用”。
- 从监控的全局高级设置进入监控设置页后，主导航的页面 ID 与显示页面一致，可直接返回监控。畸大 last_check 重置，原来有效的 snooze_until、预览和自动检查偏好均保留。
- 数据库与真实备份：在设置页修改字号尚未防抖完成时点击侧栏备份，快照内的设置已含新字号；运行受主窗口管理的 QThread 时再次点击，只显示“暂时无法备份”，快照数仍为 1。
- 针对二轮变更独立重跑 `tests/test_gui_review_regressions.py`、`tests/test_software_update_gui.py`：11 passed，其中包含实际保存详情后筛选列表消失、两种主题失败情景和五种坏时间戳。没有发现新的阻断；不据此推断 Windows 10 已通过。
