# 设计提案与迁移记录

本目录同时保存仍未实现的提案和已经完成的架构迁移记录。当前事实只能由 `docs/architecture.md`、`docs/interaction-model.md` 与 `docs/runtime.md` 断言；完成项用于解释取舍和旧能力去向。

**先找方向：**[统一信息流实施记录与后续计划](unified-event-stream.md)区分当前工作树接线、合成验证和未完成的压缩树；[长期方向与独立计划入口](long-term-roadmap.md)单列中心化聊天软件、重启补拉、数组消息与可选 LLF；[当前执行进度与下一闸门](condense-and-unify-handoff.md)仍以现有压缩／mail 主线为准。

- [新代码架构与一次性切换](code-organization.md)（已完成；调查摘要见[旧代码组织现状](../code-organization-analysis.md)）
- [Mods 两阶段加载](mods-loading.md)（已实现）
- [Mods 模块手册](module-manual.md)与[源码覆盖索引](source-coverage.md)（迁移记录）
- [Storage 加载校验与历史恢复](storage-and-history.md)（热同步已实施，剩余部分仍是提案）
- [统一消息模型：窗口、正文与双向转换](message-model.md)（已实施；行格式协议已移入[chatlog 记录格式](../../chatlog-format.md)，运行事实见[运行架构](../../architecture.md)，本篇保留取舍与被排除的做法）
- [重启后的离线消息补拉](offline-message-backfill.md)（未实现；先验证 NapCat 历史查询、锚点和分页语义，再决定窗口范围、只记账或响应、启动与续话顺序）
- [数组消息格式迁移](array-message-format.md)（长期方向，尚未切换 NapCat 或迁移 CQ 字符串消费者）
- [LLF 纯文本工具调用迁移](llf-tool-protocol.md)（长期方向，尚未替换原生工具调用与结果协议）
- [可自维护的同相上下文](self-maintaining-context.md)（未实现；记录长期记忆、外显状态转移与自修改的北极星语义及证伪条件；[讨论来源与演化索引](self-maintaining-context-discussion.md)保留原话与修正链）
- [检索索引：把子代理找过的路存下来](retrieval-index.md)（未实现；缓存子代理的检索路径而不是结论，失效判据由模型自写的校验命令给出）
- [统一信息流：读时编号与完整输出事件](unified-event-stream.md)（当前工作树已接线读时编号与受限首层覆盖，尚未提交或部署；旧档寻址、无锚覆盖和长期退休未完成，不要求 LLF 或中心化聊天软件）
- [压缩、遗忘与消息模型：交接与待办](condense-and-unify-handoff.md)（索引，不是提案；跨 chat-condense / message-model / self-message-echo 三份，回答「走到哪、下一步、谁在等谁」，未决项各自标明卡住谁）
- [聊天消息的递归压缩](chat-condense.md)（首层受限覆盖已在当前工作树接线；旧档、无锚覆盖、压力信号和物理退休仍未完成；记录已拍定项与待决语义）
- [给 agent 做一个聊天软件：主观时间轴，以及它推翻的东西](chat-client-for-an-agent.md)（后续研究，不是当前实施提案；跨窗口阅读和路径依赖上下文涉及尚无答案的遗忘问题。保留两条时间轴的论点和需复核的推论）
- [mail 与激活：把「谁进上下文」和「什么让它跑」拆开](mail-and-activation.md)（部分已实现；待办 3b 的第 3 步主动跳过，第 6 步已提交，第 7 步等实际异步回调出现再做；闸门 K 已放行，Bot 固定权限与已读激活不补轮已落地。当前转向 4a，跨窗口拉取和聊天软件路线留待以后）
- [私聊窗口的身份](window-identity.md)（已实现；`user_id` 是作者、`target_id` 是那条私聊，「谁发的」与「发到哪」不再共用一个字段）
- [自发消息回流](self-message-echo.md)（已实现；「Bot 说过的话」的写入权威从 `get_msg` 回查换成 `message_sent` 回声，`bot._route` 的自发消息关卡随之改成「记录、不派发」，`record_sent` 删除；留一条未决：同账号别的客户端发的话算不算模型说过的）
- [异常、日志与 link 报错节流](errors-and-logging.md)（按严重程度分）与[日志分流：流的身份与出口](log-streams.md)（按流的身份分；两者正交）
- [消息表示与 at/reply 解析](message-representation.md)（调查；§2 列出的五个问题已按其中 A 方案的前半修掉四个半，本篇按节标注了现状，未做的是模型侧的结构化渲染）
- [权限演进](permissions.md)（部分已实现；**模型工具已按 Bot 自身固定权限运行**，不因阅读消息改变；「代行」仍按被代行者的权限，其余演进方案尚未实施）
- [多项目共存](multicore.md)
- [模型选择的宽松解析与在线模型列表](model-selection.md)（已实现；未登记的模型照常调用，`#models` 先问对端、取不到才用本地配置）
- [op 工具集与事件注入](op-toolbox.md)（未实现；op 专属工具集：把事件交给唯一事件循环的注入原语，以及「开一轮聊天」的续话原语——`.reboot chat` 是后者的第一个消费者）
- [窗口级 `#hint`](chat-hint.md)（已实现；给聊天窗口存一段可配置代码（本质是文本管理），聊天循环停止时求值并发送——用于显示已用上下文这类运行状态，也让用户看到循环停没停）
- [浏览器、计算机使用与远程操作](browser-computer-remote.md)（未来支线备忘，不是当前提案；保存已有家底、外部调查入口和以后进入实现前需要验证的问题）

剩余提案实现后，应把已验证事实移入现行文档，并把这里改成简洁的决策记录；不要让提案与现行文档长期维护两份运行契约。
