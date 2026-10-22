# 单中心双窗口隔离宿主：交付结果

2026-10-21。T22固定worker提交`0d3352385780338cfaa9751a3ae0744e8ae6cad6`，主代理审完整三文件diff和定向证据、独立Spec/Standards必修0后集成`6db9598a2977fbc7bb6fd22451d93258b8807fa5`。主树check127 Python files/85 public modules、diff检查通过，源码hash一致，helper已done。独立worktree和生成venv/pycache已回收，accepted ref及原件保留。

新增`experiments/stateless_host.py`，局部接入原driver和具名say；原Chat、client/Ledger、output→dispatch→result继续复用。只有原commits链保存到达、激活、任期、冻结、取消处置与释放，pending/owner是投影。同root可重入锁覆盖原driver与宿主读写、首输出确认和退出检查；网络生成与假发送在锁外。不可复用epoch拒绝重复run和旧取消/收尾；正常pending可用同一健康client继续，取消/异常关闭原会话，新pending只保留待显式恢复。

只有显式say才调用假出口；原参数target必须属于两个合成群窗口，端点确认message ID且实际final_call为真才正常结束，整批工具结果先完成。恢复新增狭窄final-action证据分支，旧无工具最终输出分支保留。退出通知只尝试一次并发向固定origin；实际端点确认、落盘say结果、请求usage和请求中的screen记录曝光数分列，关联窗口不用作成功发送量。

## 实际验证与修补证据

25个最终有限SDK Mock单案、36请求通过，覆盖两个到达方竞争、同epoch重复run、退出前/交界/后到达、首输出前失败与确认后新激活、两处取消、旧finally、新pending、say B/final但通知A、无ID/超时/失败、结果保存失败、收尾失败及坏日志。各案源hash与固定提交一致；默认无host driver另4请求通过。每命令独立timeout60、512MiB地址空间上限、串行执行；当前VmHWM最高89148KiB，默认回归83740KiB。没有付费、真实网络或生产状态读取。

保留`cleanup-failure-1.log`：原探针错期望ValueError，真实fixture missing-usage路径抛RuntimeError；修正断言后证明原异常不被收尾LookupError覆盖，首个失败内存不可恢复。Spec又发现原错误target伪造可能提前在旧terminal cutoff被拒，不能独证目标校验；原记录保留。补充探针重建全部sequence/parent/cutoff，未改对照head/terminal一致通过，错误target/action/缺批result均明确以final-call证据不足拒绝，独立增量复核通过。产品提交未因此改变。

[交付原件](/root/work/agent-memory-active-continuation-20261020/T22/delivery.md)、[25案与源码索引](/root/work/agent-memory-active-continuation-20261020/T22/verification-index.json)、[Standards](/root/work/agent-memory-active-continuation-20261020/T22/standards-review.json)、[Spec原审查](/root/work/agent-memory-active-continuation-20261020/T22/spec-review.json)、[Spec补充复核](/root/work/agent-memory-active-continuation-20261020/T22/spec-supplement-review.json)、[主代理集成](/root/work/agent-memory-active-continuation-20261020/T22/integration.json)、[清理回执](/root/work/agent-memory-active-continuation-20261020/T22/cleanup.json)。

## 仍未接管

这是单进程、固定双合成群材料池、假出口；不含未知实时bytes、私聊、note writer联合接线、旧oplog/arrival/TTL迁移、生产waiter/watchdog、hint正式event_count、QQ回声、持久重访和自动重启续接。取消使用协作检查点，不承诺中断已在途的调用。持久化未知后只允许本进程一次假退出通知例外，不修坏日志或重放工具，也不是重启安全投递。

这补工程生命周期证据，不证明D12整链、主体性或任何生产机制可删。下一交付收敛为生产接入与迁移回退决策包，缺证据可使结论为不足切换；不自动再开真实语义采样或堆叠实验票。
