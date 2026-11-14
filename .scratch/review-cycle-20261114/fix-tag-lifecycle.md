# 标签统计生命周期修复

状态：修复与定向验证完成，等待主代理独立复审；已释放编辑权。分支沿用 fix/review-hardening-20261114，父议题和审查基线沿用本轮 review；未 commit。

## 范围与任务清单

- [x] 真实 Qt 事件循环复现页面销毁时后台线程访问已删除 C++ 对象。
- [x] 采用页面拥有的单次 QTimer，合并 load_data 延迟请求。
- [x] 活跃统计线程不被新请求覆盖；线程退出后合并补刷新。
- [x] 工作线程只持有 DB 与独立无 parent 的 QObject 信号桥，通过 Qt slot 回到 GUI。
- [x] DB 异常捕获记录，异常后可再次刷新。
- [x] 新生命周期测试与现有标签、工作视图、响应布局相关测试通过。

只修改 `gui/views/tags_library.py`，新增 `tests/test_review_tag_refresh_lifecycle.py`；本报告为 scratch 记录。未修改 core/manual_test_environment.py、其他代理文件、依赖或标签业务规则；未删除文件。回滚边界为本次两个代码/测试文件的差异。

## 修复

TagsView 构造时建立 `QTimer(self)`（单次、500ms），每次 load_data 重启同一个计时器。直接刷新会消费已有计时请求；若当前线程仍存活则只重启计时器，等待结束后补刷新，最大同时运行一个任务。

后台工作使用模块函数，参数仅为 DB 与 `_UsageRefreshBridge`，没有 TagsView 或其 bound method。桥没有 Qt parent，工作线程参数持有强引用；桥信号连接页面的 `@Slot(bool)`，接收者销毁后由 Qt 自动断开。线程不再调用 QWidget，不再后台改写 `_usage_refresh_thread`。GUI 保留已结束引用，兼容现有退出等待器。

DB 统计异常被捕获并记录异常类型，避免异常进入 threading.excepthook；仅成功时通知页面读取统计结果。页面的滚动位置等原 load_data_silent 行为不变。

## 红绿证据

命令：`rtk proxy python -X utf8 -m pytest tests/test_review_tag_refresh_lifecycle.py -q -rs`

1. 延迟触发前真实 deleteLater + DeferredDelete 销毁：旧实现已通过，保留为防回归。
2. 第一轮增加“DB 阻塞中销毁页面，再放行真实线程”：旧实现 **1 failed / 1 passed**。捕获到 `RuntimeError('Internal C++ object (TagsView) already deleted.')`；独立信号桥修复后 **2 passed**。
3. 第二轮增加“阻塞中六次 load_data 与直接刷新”：第一阶段实现 **1 failed / 2 passed**，实际峰值 **14 并发**。加入 owner timer 与活跃线程门禁后 **3 passed**，峰值为 1，活跃线程引用未覆盖，全部请求合并为一次后续刷新。
4. 增加 DB 异常无逃逸、错误日志、异常后真实 SQLite 重算并更新模型为已知使用次数 2，以及延迟期间五次 load 合并一次：**5 passed in 7.16s**。

测试不替换 Qt timer、QObject 或 QWidget 销毁，不替换 Python Thread；只在数据库边界注入 Event 阻塞/异常以控制真实生命周期。实际成功统计路径仍执行原 DB 方法。

## 最终验证

```text
rtk proxy python -X utf8 -m pytest tests/test_review_tag_refresh_lifecycle.py tests/test_tag_work_views.py tests/test_tag_vocab.py tests/test_tag_normalize.py tests/test_tag_import_service.py tests/test_tag_group_ops.py tests/test_tag_group_heal.py tests/test_tag_config_groups.py tests/test_tag_ai_worker.py tests/test_tag_ai_assist.py tests/test_review_responsive_layout.py tests/test_layout_search_fixes.py tests/test_analysis_tagging_bugs.py -q -rs --junitxml=.scratch/review-cycle-20261114/fix-tag-lifecycle-junit.xml
109 passed, 51 warnings in 49.64s

rtk proxy python -X utf8 -m ruff check tests/test_review_tag_refresh_lifecycle.py
All checks passed!

rtk git diff --check -- gui/views/tags_library.py tests/test_review_tag_refresh_lifecycle.py
通过
```

51 条警告均来自 `gui/models/tag_model.py:14` 的 invalidateFilter 弃用提示，未出现 PytestUnhandledThreadExceptionWarning。对修改页面运行 Ruff 仍有原存的 12 项 F401/F811；通过从 HEAD 读取原文件、以 Ruff stdin 检查验证基线同为这 12 项，未顺手修改。

## 环境与边界

Windows 11 x64、Python 3.13.12、PySide6 6.10.2；Qt 离屏真实事件循环。没有 Windows 10 实机或可见桌面长时间压测证据。已运行任务在页面销毁后会安全完成 DB 统计；本修复没有实现数据库操作强制中断。测试均使用临时数据库与合成记录，不访问用户素材或公网 AI。
