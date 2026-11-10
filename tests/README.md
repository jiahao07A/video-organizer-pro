# 测试运行与维护

在项目根目录通过 PowerShell 7（`pwsh.exe`）运行。使用当前已安装的项目依赖与 pytest；不需要真实供应商密钥或真实素材库。

```powershell
rtk proxy python -X utf8 -m pytest -q -rs
rtk proxy python -X utf8 -m pytest tests/test_analysis_failure_center.py -q
rtk proxy python -X utf8 -m pytest --collect-only -q
rtk proxy python -X utf8 -m ruff check tests --select F401,F811,F821
```

`pytest.ini` 将默认收集范围限定在 `tests/`。`conftest.py` 在测试模块加载前默认设置 Qt 离屏平台，`app` 与 `qt_app` 共用一个会话级 QApplication，保持强引用。GUI测试仍需关闭自己创建的窗口，停止定时器并等待自己启动的工作线程；共享 QApplication 不代替资源清理。离屏测试不能确认桌面缩放、多显示器与真实操作手感。

测试模块按业务能力命名。`support/` 保存多个模块共享的模拟客户端、服务fixture、签名数据与本地HTTP辅助；不要从其他 `test_*.py` 导入fixture或辅助函数。导入pytest fixture时采用显式重新导出，如 `from tests.support.recovery import service as service`，避免静态清理误删。不同业务的service fixture保持各自设置，不用一个全局fixture隐藏差异。

构造服务时使用 `SettingsManager.deep_copy_defaults()` 或 `copy.deepcopy(DEFAULT_SETTINGS)`，并显式指定临时数据库路径。禁止用 `{**DEFAULT_SETTINGS}` 浅拷贝后修改嵌套字段。测试文件、导出物、配置及数据库使用 `tmp_path`；视频用合成内容，API用模拟传输或回环服务器，不读取用户素材或调用真实外网API。

断言应验证产品入口的实际结果，避免在测试里重写被测算法。不要用 `or True` 或“计数非负”代替预期写入断言；构造路径变体时应确认输入确实不同。子进程和事件轮询需要明确超时。

查询测试应同时放入匹配与不匹配记录，大范围测试必须实际入库；列表、统计与重试目标应保持一致。配置测试读取服务实际绑定的路径，分别比较内存和磁盘，必要时重新创建服务验证读取。持久化测试读取主文件并核对业务字段，动态耗时与时间戳不用于跨时刻精确比较。抽帧回归测试调用真实抽帧入口；AI脱敏测试捕获发送参数，既检查敏感内容缺失，也检查必要技术上下文仍被保留。

## 压力测试与跳过项

`test_analysis_stress.py` 的9个参数化场景默认跳过，显式启用后只向临时回环HTTP服务器发请求，包含512任务及最高256并发，会占用本机资源。通过下面命令仅为本次Python进程启用，不改变终端后续测试环境：

```powershell
rtk proxy python -X utf8 -c "import os,pytest; os.environ['VOP_RUN_STRESS']='1'; raise SystemExit(pytest.main(['tests/test_analysis_stress.py','-q','-rs']))"
```

压力结果保存在 `.release-build/perf-stress-results.json`，属于本地产物。Windows路径与进程标志测试只在对应平台执行；媒体预览测试在Qt Multimedia或ffmpeg不可用时说明原因并跳过。使用 `-rs` 查看实际原因；不得为得到通过结果而扩大跳过条件。既有Qt弃用警告保留可见，不全局屏蔽。
