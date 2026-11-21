# platform 第二轮执行包

Status: claimed

实施任务：负责两个接口、两个基准工具和时间测试。1) request_interval+音频转录 在分析管线使音频请求未发出，修复节流回调调用/取消，SDK回环实测。2) 设置页“验证并刷新模型”的 fn 调用 self.service.ai.list_models 不存在（gateway 有），让 UI 到真实 gateway 获取当前编辑供应商的模型；避免改变默认路由；实际回环 server 和 UI 按钮测试而非 mock 成功。3) gui_performance_benchmark.py、video_catalog_benchmark.py 修复 F821、新增正确基准首屏行数（50条被错误报告0）、若查询未完成显式失败并正确清理窗口线程、隔离测试数据，运行基准。4) test_analysis_job_metrics.py 现测试在忽略 started_at 的变异下仍绿，使用固定时钟和精确期望，变异必须失败。5) 补正被误命名为 require_explicit_save 的密钥用例，按现行设置自动保存真实规范在实际防抖后的磁盘/服务重载核对，不要将产品改为手动保存，若已有同等用例可合理精简。先红后绿，不提交。

全部命令使用 pwsh.exe 和 rtk；真实素材及用户配置禁止用于测试。收到空白派发通知时亦可按此文件实施；只有主审可以更新所有任务状态。
