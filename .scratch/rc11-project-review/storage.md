# storage 第二轮执行包

Status: claimed

实施任务：负责媒体保真、哈希与监控性能。1) MetadataInjector.inject_tags 保留输入容器、显式映射全部流/章节/元数据，对不能保真写回的容器或不支持的流保留原件（禁止报告成功）。真实双音轨 MKV 和 MP4、容器、comment、音轨、失败原件。2) filter_processable_paths 当前 analysis 的记录发现与随后资格校验立即重复做全文件哈希；只消除相邻、可证实的重复读取，抛错仍校验，保留后续并发变化/提交前完整校验。不引入可能失真的长期缓存。3) media_watch 对300条已登记且未变的文件，补扫打开了605个DB连接（.release-build/review-rc11-storage/perf_probe.py）；复用既有 db.reuse_connection 或分批查询，不能持 SQLite 长写事务。4) 新发现20万行 list_page 带 COUNT(*) OVER() 将操作所有匹配大行且不能直接利用排序索引，默认首屏中位1359ms。只修改 core/video_catalog.py 对于默认（或合理选择查询情况）改一条SQL使用不相关标量子查询 count + ORDER BY LIMIT，原子快照、空页总数、稳定次序/筛选不能变，避免对筛选查询执行两遍很慢的 LIKE；量化前后结果。不更改存储格式。5) 核对Windows Unicode路径：_sql_path_key 仅SQLite内置LOWER，Python normcase 支持非ASCII，已存在素材 Ü/ä 可能不在工作范围。独立重现，若成立用已有数据库连接工厂注册与Python一致的确定性路径函数并用于SQL；加包含/排除/相邻目录回归。各点用独立新回归测试；同一服务其它区块别人修改，不得覆盖整文件。

全部命令使用 pwsh.exe 和 rtk；真实素材及用户配置禁止用于测试。收到空白派发通知时亦可按此文件实施；只有主审可以更新所有任务状态。
