# rc6提交、合并与发布

Status: resolved
Issue: #65
Branch: codex/tests-audit

2026-11-10用户明确授权commit、合并、发布Release。范围为此前已验收的失败中心、工作流和UX修复及测试清理；不提交本地探针、运行数据或私有数据库。沿用0.0.1-rc6，发布预发布渠道。

- [x] 审核暂存并提交功能和测试两个检查点（768d49b、e532caf）
- [x] PR #66经rebase合并main，合并提交5547a6cadbe029cf104fbb3cd1012d21f9f1dba8；无配置的远端CI，不虚构检查结果
- [x] Ed25519签名清单、草稿上传下载回验后公开rc6预发布
- [x] 无登录公开链接重新下载4个资产，内置公钥验签与安装包摘要通过；补齐关联议题和记录

Release：https://github.com/jiahao07A/video-organizer-pro/releases/tag/v0.0.1-rc6
安装包104732059字节；SHA256：9895efcba9799738a9034dfe58c04c6c1f70fb45974cd10b27ecc6dd03464fcc。其余公开资产为update.json、update.json.sig、SHA256SUMS.txt。标签指向已验证产品提交，不修改历史标签。

验收：全量669 passed、9 skipped；原677场景完整保留并新增1项；7种变异均拦截；Ruff及diff检查通过；包内68模块与源码一致，隔离启动退出通过。数据库回退必须恢复升级前备份。
