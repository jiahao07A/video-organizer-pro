# api 第二轮执行包

Status: claimed

实施任务：接手桌面首审的四项与平台时间戳。主窗口侧栏备份复用设置页 flush/忙碌/监控维护门禁；侧栏主题与设置同步、保存到绑定目录且失败不假成功；监控全局高级设置同步主导航和设置页；素材库详情保存更新真实列表及筛选。gui/services/update_controller.py：损坏/越界 last_check 或 snooze_until（负数、bool、NaN、无穷、Windows 不支持的年份）安全降级，保持其他健康偏好。读 .release-build/review-rc11-desktop/probe.py/details.py（两个独立文件）。先红后绿，写 tests/test_rc11_desktop.py 等，文件范围 gui/main_window.py、gui/views/material_library.py、gui/views/settings.py 相应区域、更新控制器。勿改 API 或他人代码，勿提交。

全部命令使用 pwsh.exe 和 rtk；真实素材及用户配置禁止用于测试。收到空白派发通知时亦可按此文件实施；只有主审可以更新所有任务状态。
