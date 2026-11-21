# 独立复核：命令行与导出

Status: claimed

数据代理完成自己的二轮修改后，独立审查 desktop 的 video_organizer.py、core/video_organizer_service.py 导出函数、gui/views/workstation.py、installer 与新增测试。用首轮探针复测范围外文件哨兵、空集合、可见范围/副本、dry-run、ALE 特殊字符、JSON/CSV 导出可用和目的地（防覆盖现有源素材、管理数据文件），以及卸载器修改证据边界。只读，不执行会批量删除项目或用户文件的卸载。把结果和真实缺陷写在本文件末尾，禁止提交。

## 独立复核结果

- 首轮 probe.py 复制隔离当前源码重新执行：空目录 --rename / --dry-run 都返回 1、外部视频和侧车均未改动。ALE 的 CR/LF/TAB 被压成单格空格，列顺序遵循设置（探针默认四列，实际一行四格）。FCPXML 继续通过 1.7 DTD。
- 新的 JSON/CSV 服务用例确认冻结选集和副本门禁，GUI 用实际搜索后从菜单导出，仅产生可见项；空集保持既有目标不变，源 mp4/活动设置路径被拒绝。7 项交叉执行中 6 项通过，另 1 项有以下可复现问题。
- **P2 退回作者**：新用例 test_cli_real_preview_and_rename_stay_in_requested_directory 使用 TAB 类别/标签，预览允许但实改 WinError 123（FileManager.sanitize_filename 未去控制字符）。没有越界改名，原文件保留。请求作者补 Windows 控制字符清理后重跑，不删除失败用例。
- **P1 退回作者**：protected_probe.py 构造程序旁的 .video-organizer-data.json 和 .video-organizer-install.json，导出均返回 1，哨兵被覆盖。已在主审第三轮执行包记录对实际管理路径的保护，等待作者修复及复核。
- 安装脚本 diff 已删除递归清理 _internal，仅按安装清单卸载文件，额外移除安装标记的单个路径。此处仅验证脚本证据，不声称修复后安装器已实机卸载，未执行卸载。

### 第三轮复测

- 作者修复后，独立执行 tests/test_review_cli_result_exports.py：12 passed，1 条已知 Qt 弃用警告。TAB/C0 清理、CLI 真实改名/范围、ALE 特殊字段、JSON/CSV 内容/副本/空集、界面筛选导出和 FCPXML 全副本不提示成功均通过。
- protected_probe.py 绑定隔离实际应用管理目录，两个内部 JSON 目标均抛 ValueError，原哨兵字节不变。普通目录中的同名文件允许输出，避免误拦用户文件。
- 本复核没有剩余可复现阻断。安装器卸载部分仍只有源码证据，需主审核实构建与隔离安装边界。
