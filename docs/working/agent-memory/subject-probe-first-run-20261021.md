# 主体连续性首段真实诊断：已有局部链，末次解析中断

2026-10-21（北京时间）。对应[D12](decisions/D12-经验能否在后续实际消费并可逆修订.md)、[冻结运行候选](subject-probe-first-run-plan.md)。实际运行固定主树`f54585e`，只使用中性合成工作台与真实DeepSeek官方模型，不含生产记忆、聊天或柚子身份迁移。没有旧新四臂或预置兴趣答案。

## 观察到什么

真实模型形成并可靠提交了两版笔记。第一醒选择保持现状、不移动物件、不写卡片，也未请求重访；第二醒获得harness提供的机会，读到环境变化后校正先前表述，写入第二版，并选择移动一个物件、请求稍后回来做一次检验。第三醒确由这次实际工具请求触发，而非脚本免费补醒。

第一版在第二醒、第二版在第三醒的真实HTTP请求中均准确曝光；显式read_note返回与后续wire也一致。第二醒既有引用旧笔记的表述，也有根据实际反馈修订的行为。因此局部链已出现：模型形成→受控提交→后醒准确曝光→有混杂的使用迹象→一次反馈修订→实际请求重访→独立结果进入新请求。

这仍不是笔记独立作用的因果证明。原行动历史和当前观察同时提供了相同信息；仅凭这段不能把后续选择归因于note，更不能推论长期学习、一般自主性或主观意识。第二版sources没有列出当次observe坐标，正文观察可由真实请求交叉核验，但来源元数据本身不能单独重建完整观察链。此限制不改写成来源伪造或已具逐句溯源保证。

## 在哪里中断

第三醒的物件实验结果已经返回并进入第10次请求，但该次响应后非流式解析发生异常，全会话按设计闭锁为`parser-failure`。没有有效的后续解释、第三版笔记或进一步行动，原根没有重试或恢复执行。未见迁移、长期保留等环节是未取得证据，不能据此判成模型选择失败。

第10次响应usage已可靠结算；completion达到4096上限，其中3340为思考tokens。生产非流式解析器会拒绝`length`等结束原因，但现有response证据只有usage、身份和body hash，没有finish_reason或原始解析异常。因此“输出截断导致”目前只是候选解释，不能确诊，也不先把输出上限调大。

[T18](tickets/T18-保留非流式解析结束原因并定位实验内存增长.md)已在`591c6c3`验收：在既有凭据反射守卫之后保存结束原因，保留非CapacityStop异常的类型/结构帧和初始化、分阶段内存；依然先结算、未知闭锁、不重试。15个实际SDK固定Mock定向案、前后smoke及主树check通过，两轴审查0必须修。不改生产parser、原Ledger权威或本段已冻结上限。后续真实诊断另冻新根及实际预算，不能修改本段hash续跑。

## 费用、资源与凭据

- 本段3醒、10个持久请求行，已知82,884 tokens，未知0；冻结保守价表折算132,398微元，即0.132398元。不是供应商实付；另一次metadata GET的计费仍未知。
- 共同100M权威已实际预占并对账，释放未用1,917,116 tokens；累计已知154,294 tokens，可用99,845,706。原Ledger仍为请求结算权威，没有按角色另领余额。
- 执行87.1秒，单线程；早期`/proc`的VmRSS/VmHWM均为88,092KiB（约86MiB），结束时`ru_maxrss`为668,224KiB。后者可能保留同进程exec之前的峰值，不能据此断言本次Python达到约653MiB或超过512MiB。缺少起始ru_maxrss和结束VmHWM，本次Python峰值及是否增长均未知；没有本次OOM证据。原测量和此前超阈值判断保留，解释修订见[资源口径补证](/root/work/agent-memory-active-continuation-20261020/D12-runpack/resource-interpretation-20261021.json)。
- 本机16MiB标准库探针复现同PID在exec后VmHWM回落至12,136KiB、ru_maxrss仍保留28,288KiB；只证实测量差异，不证明首段668,224的来源。另对10次请求的console格式化做隔离检查，耗时0.08秒、RSS14,448KiB；该检查不覆盖完整SDK与真实运行。后续同时记录初始化及分阶段的VmRSS、VmHWM、ru_maxrss，不再用单个进程历史峰值判定增长。
- 原operator脚本及hash保留；后续operator将控制台输出写到`/dev/null`并加资源限制，未执行模板与原脚本分开保留。4号机本次只读连通性检查仍超时，不循环重连；本地仅串行轻量离线诊断，不再次冲内存。
- 维护者本次直接提供临时凭据，关闭echo后仅经stdin入进程内存，进程已退出。未写key文件、argv、环境、票据或运行产物；特指产物的凭据形状扫描0。没有读取生产配置/聊天、发送QQ或重启。

## 可复核证据

私有合成运行根`/root/work/agent-memory-subject-first-20261021`完整保留。两版版本与全文SHA分别为`1e137b58… / 773c85f7…`、`2b118f1d… / ccc9a1eb…`；具体commit/request坐标见[轨迹独立复核](/root/work/agent-memory-active-continuation-20261020/D12-runpack/trajectory-review.md)。

[运行摘要](/root/work/agent-memory-active-continuation-20261020/D12-runpack/execution-summary.json)、[实际预占](/root/work/agent-memory-active-continuation-20261020/D12-runpack/preallocation-receipt.json)、[实际结算](/root/work/agent-memory-active-continuation-20261020/D12-runpack/settlement-receipt.json)、[失败定向核查](/root/work/agent-memory-active-continuation-20261020/D12-runpack/failure-triage.md)和[完整性/资源观察](/root/work/agent-memory-active-continuation-20261020/D12-runpack/execution-integrity.json)分开记录实际事实与未知。今天重新核对的[官方价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)和[元数据接口定义](https://api-docs.deepseek.com/api/list-models/)与冻结合同一致，随后实际GET也验证了目标容量。

D12继续未裁定。T18诊断补齐已验收，缺少的有效解释/后续修订及未见迁移证据继续推进；本段不支持D13旧机制退役、D14生产切换或整个主体目标已完成。
