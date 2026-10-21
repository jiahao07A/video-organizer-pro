# 临时人工测试环境

项目提供 `tools/manual_gui_test.py` 作为人工走查入口。它启动一个独立的临时会话，不读取项目根目录的数据库、设置、标签配置或分析结果。

## 启动可见 GUI

在项目根目录执行：

```powershell
python tools/manual_gui_test.py --records 8
```

脚本会预置合成素材记录并打开桌面界面。合成媒体是占位文件，不是真实视频；因此适合检查列表、筛选、详情、标签库、设置和数据目录入口，不适合验证视频解码、抽帧或真实 AI 分析。

常用选项：

```powershell
# 预置更多记录
python tools/manual_gui_test.py --records 20

# 不创建占位媒体文件，只检查数据库记录和界面
python tools/manual_gui_test.py --records 20 --no-media-files

# 在没有桌面的环境中验证启动和清理流程
python tools/manual_gui_test.py --records 8 --offscreen
```

## 生命周期和数据边界

会话启动时在系统临时目录创建独立根目录，包含：

- 临时 SQLite 数据库；
- 临时 `settings.json`、`tag_config.json` 和结果文件；
- 合成素材占位文件；
- 临时缩略图和备份目录。

窗口关闭后，脚本会请求取消分析、等待可见后台线程，再删除上述临时根目录。正常关闭后，终端会显示“临时数据已清理：是”。

诊断数据保存在：

```text
logs/manual-tests/<session-id>/
```

其中包含应用日志、会话元数据、分析任务指标（如产生）和 `cleanup-report.json`。诊断目录故意保留，便于排查人工测试问题；它不包含临时数据库或合成素材的复制品。

如果 Windows 上有外部程序仍占用临时文件，清理报告会记录失败和临时路径，脚本不会把失败伪装成成功。确认没有程序使用该路径后，可以按报告手动删除。

## 建议人工走查顺序

1. 打开“素材库”，确认看到预置的合成素材记录。
2. 检查列表选中、搜索、筛选和详情面板，不应出现工作区既有素材。
3. 打开“工作台”，确认预置记录位于临时会话的合成素材工作范围内。
4. 打开“标签库”，检查标准词和标签组；只使用合成词进行编辑测试。
5. 打开“系统设置”，检查普通界面偏好保存和恢复；不要填写真实 API Key。
6. 关闭窗口，确认临时数据已删除，诊断目录仍保留。

临时环境也可在 Python 测试或脚本中直接使用：

```python
from core.manual_test_environment import ManualTestEnvironment

with ManualTestEnvironment(record_count=4) as environment:
    service = environment.service
    # 只使用 service 和 environment.paths 指向的临时数据。
```

所有会改变原始视频、调用外部 AI 或需要真实解码的验收，都应另行使用可恢复的测试副本，不应把本脚本生成的占位文件当作真实视频能力证明。
