# 旧合成格式经新快照反查：实际交集

2026-10-21，主代理在T22交还运行槽后单次执行仓库外探针，exit0、12项断言通过。仅AST提取已有`legacy_synthetic_package`的三天字面量和DATES，实际调用`comparison_snapshot.freeze_sources/open_snapshot/resolve_ref`及原parser，没有用替代解析器或运行整套A0 doctor。源码pins及导入源码hash保存在结果；相关读取源未被T22修改。

| 检查 | 实际结论 |
|---|---|
| 每个显式event_key | 原UTF8切片、SHA和可见origin逐项相等，不把解析文本改写成“原文” |
| 同秒负重复ID | 仅ID、ID加同时间两种查法均拒绝；event_key仍可精确读 |
| 跨日重复ID与开放边界 | 只开首日时唯一旧ID归首日，后日event_key拒读；全开后重复ID拒绝，不默换来源 |
| CRLF与空白 | raw保留CRLF、内部CR及原空白；解析message另有规范化，两者分别核对 |
| v0/v1声明 | 相同头部必须显式声明版本，原record版本与frozen身份区分 |
| 输入边界 | 缺synthetic、未知版本、额外private字段、非bytes均拒绝；拒绝额外字段不等于实现私聊 |

共12项含同秒查法的两次断言，不宣称12类格式已覆盖。耗时2.156秒，当前VmHWM20164→71716KiB，512MiB地址空间上限。公开SDK库有导入，构造/请求/HTTP send均有拒绝哨兵且实际调用0；socket/subprocess和生产读写受audit拒绝，denial0、无付费。脚本只新建结果JSON，不写任何生产或仓库业务文件。

静态预审先发现原探针禁止SDK import会挡住必需的真实模块导入，因此在实际执行前修正为“允许库、禁止构造/调用”；原脚本与说明均保留，未将其当实际失败或产品缺陷。没有重跑到通过。

交集只限显式synthetic普通group v0/v1；私聊、sidecar、混合切换日、新旧archive完整性、旧oplog/arrival/pending/TTL、动态.py/link/#hint及生产存量均未证明。新journal恢复11案和此格式交集也不能共同替代那些缺口，不获迁移或删除许可。

[定向补证](/root/work/agent-memory-active-continuation-20261020/D13-legacy-readiness-20261021.md)、[实际探针](/root/work/agent-memory-active-continuation-20261020/D13-legacy-probe-20261021/probe.py)、[结果与source hashes](/root/work/agent-memory-active-continuation-20261020/D13-legacy-probe-20261021/result.json)、[准备修订历史](/root/work/agent-memory-active-continuation-20261020/D13-legacy-probe-20261021/preparation-revision.json)、[新进程恢复](recovery-process-evidence-20261021.md)。
