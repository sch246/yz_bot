# 新解释器只读恢复：有限实际证据

2026-10-21，固定acb6eca及stateless_loop源码SHA`d8f9e08c71e07cd7160a4e31cb51f1ea0db6f60967364a6183c750226c6c05a0`。仅用仓库外一次性脚本独立构造合成journal前缀，调用原recover_commits；没有修改仓库代码或新增永久测试，没有模型/凭据/生产访问。

| 独立前缀 | 实际结果 |
|---|---|
| output-only | 原边界/ack精确保留，两工具not-dispatched |
| dispatch-only | 已派发无结果为unknown，后一工具not-dispatched |
| 部分result＋取消terminal | 第一工具completed，后一未派发，terminal仍cancelled |
| 受支持的无host历史形状 | 正常读取 |
| 部分host字段、重复request、重复Chat首次身份、stale generation | 各按原校验拒绝 |
| terminal后result | 拒绝action after terminal wake |
| .new残尾、persistence-unknown标记 | 均拒绝，未修复或截断 |

三个不同PID的新解释器串行exit0，1.31/1.31/1.45秒，每进程timeout60和512MiB地址空间上限；当前映像VmHWM最高64696KiB。11案所有原文件哈希前后相同。audit拒写/socket/新进程，另在append/durable/Chat/Ledger/drive/finish/close设置哨兵，命中0；恢复纯读取路径本身没有工具派发。合法返回automatic_requests=0、automatic_tools=0。主代理亲读脚本、恢复schema和实际输出，再对保存输出作11案独立断言，没有重跑SDK或实验。

[脚本与原件](/root/work/agent-memory-active-continuation-20261020/D13-recovery-20261021/verify.py)、[子进程结果](/root/work/agent-memory-active-continuation-20261020/D13-recovery-20261021/execution.json)、[逐案摘要](/root/work/agent-memory-active-continuation-20261020/D13-recovery-20261021/verification-summary.json)、[主代理复核](/root/work/agent-memory-active-continuation-20261020/D13-recovery-20261021/root-review.json)。原始三份stdout、异常及每个前缀都在同目录。

这补上隔离新进程读日志边界，不证明真实Bot重启、supervisor、旧oplog/arrival格式转换、现存pending/TTL、QQ路由/回声、动态消费者、物理断电或自动续跑。当前没有对应双窗口宿主调度实现；不得把静态串行alpha/beta fixture当成该功能。D13继续open，生产接管和删除均未获结论。
