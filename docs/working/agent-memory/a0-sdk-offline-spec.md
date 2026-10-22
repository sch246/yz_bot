# A0 SDK 假传输校准范围

承接 [T01](tickets/T01-实施A0合成离线适配与无模型校准.md) 与 [D05](decisions/D05-裁定首轮验收取舍-样本范围和预算.md)。维护者在T01完成后要求“继续”，并明确选择后续A0小试固定DeepSeek官方直连。本阶段继续实验代码与无模型校准；供应商选择不授权真实材料/凭据读取或扣费。

## 交付边界

新增 experiments 内的真实OpenAI Python SDK与假HTTP传输纵向切片。只创建带固定虚构key、httpx.MockTransport、trust_env=False、max_retries=0的SDK实例；构造器不提供可注入普通网络transport/真实key或配置路径。独立入口只能运行内建fixture，无真实HTTP、模型、QQ、材料或生产代码修改。T01保留不重写；新票不表示D05/D10完成。

复用真实LLMClient生成/解析与至少一条A0工具调度路径，不以手写简化parser冒充生产接缝。所有消息/usage/故障均合成；官方公开契约见原票最新研究证据。

本切片显式采用`persist_reasoning=True`，T01的False不改。[后续补证](a0-projection-and-input-bound.md)确认：当前中心A0的keep/同model原生路径下，False也保存reasoning，两个实参不能直接命名为不同历史策略。manifest须同时记录实参、reasoning设置与实际请求投影；当前没有冻结真实A0基线，也不改变[无状态提案§6的D4](../proposals/stateless-agent-loop.md#6-维护者的裁定2026-10-19)。

## 请求、结算与停止

- 在SDK最终序列化请求的发送边界捕获完整body、model、path与参数，持久预留再进入fake传输；不能只hash尚未加入max_tokens/stream_options的request_policy参数。唯一pilot/request关联，一次预留最多一次fake传输；single flight，无fallback/自动恢复/补收尾请求。
- 接口目标为官方Chat Completions，wire model deepseek-flash。固定请求参数，显式4096输出、thinking/high与auto工具选择；standard endpoint不误开beta strict，不提供服务端Search/Fetch或图像能力。
- 观察SDK原始流/响应usage，再交既有parser；length/断流/异常/取消/generator close仍保留已经取得的usage，无usage按全预留。缓存字段不重复计数；已知usage互相矛盾或非法则保留原始合成证据并闭锁，不能归零。所有工具输出须缓冲至请求结算成功，超报或结算持久化失败不派发工具。
- 本阶段账本仍明确synthetic模式；可以用官方价格数字的离线快照校验算术，不能借此放开实付。预留输入计算只声明fake接口边界，不将T01 bytes+1024当真实模型token上界。18万输入/4096输出/12次与高峰价4.713216元仅为未来pilot候选；真实输入计量与费用授权依然缺失。
- 部分恢复/fork继续明确拒绝，不新增生产循环、四臂、笔记或通用调度框架。不修改mods；若无法复用原parser且保持上述边界，报告具体证据，由主代理处理实验范围取舍。

## 必须取得的假传输证据

1. 正常流中reasoning/tool deltas及最后一个choices非空的usage chunk，验证最终body含max_tokens、stream_options、schema与全部历史；至少连续两轮工具调用，再检查第三次最终body，前两轮assistant的合成reasoning原值及tool_call_id/结果配对均保留，并覆盖A0重建路径。冻结SDK版本及代码/协议来源；至少一条真实A0工具路径实际消费结果。
2. 连接异常、429、5xx分别只有一次fake传输和一次预留，无重试/回退；12次后第13次在fake传输前拒绝，不能只数逻辑请求。
3. 含usage的length、usage前/后断流、取消或关闭generator：已经取得的usage准确保留，缺usage全预留，无修补调用，流关闭且请求只结算一次。
4. 计费字段含cache别名、reasoning分项、missing/冲突/超报、账本落盘失败：账本可对账且异常工具不得派发；先前T01工具中途取消/日志失败证据继续有效，不冒充新测。
5. 所有HTTP均命中mock，socket/QQ/生产写哨兵为0；环境中的key/proxy/config不会被解析，模块/ContextVar/当前事件等绑定退出恢复。拒绝真实输入/credential/live运行参数。
6. 合成输入预留单独合格、加4096输出后越过联合上下文边界时，必须在mock传输前拒绝。这只验证假接口政策，不证明真实DeepSeek输入计数上界。

结果写仓库外私有合成目录；票据保存脱敏断言/指纹/命令。worker在独立worktree交付commit，主代理审查集成；按仓库要求check/smoke及文档校验。已通过而未受影响的旧证据不机械重跑，必要的新doctor与原A0 doctor可组合一次回归。
