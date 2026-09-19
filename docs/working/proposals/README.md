# 设计提案与迁移记录

本目录同时保存仍未实现的提案和已经完成的架构迁移记录。当前事实只能由 `docs/architecture.md`、`docs/interaction-model.md` 与 `docs/runtime.md` 断言；完成项用于解释取舍和旧能力去向。

- [新代码架构与一次性切换](code-organization.md)（已完成；调查摘要见[旧代码组织现状](../code-organization-analysis.md)）
- [Mods 两阶段加载](mods-loading.md)（已实现）
- [Mods 模块手册](module-manual.md)与[源码覆盖索引](source-coverage.md)（迁移记录）
- [Storage 加载校验与历史恢复](storage-and-history.md)（热同步已实施，剩余部分仍是提案）
- [统一消息模型：窗口、正文与双向转换](message-model.md)（已实施；行格式协议已移入[chatlog 记录格式](../../chatlog-format.md)，运行事实见[运行架构](../../architecture.md)，本篇保留取舍与被排除的做法）
- [可自维护的同相上下文](self-maintaining-context.md)（未实现；记录长期记忆、外显状态转移与自修改的北极星语义及证伪条件；[讨论来源与演化索引](self-maintaining-context-discussion.md)保留原话与修正链）
- [检索索引：把子代理找过的路存下来](retrieval-index.md)（未实现；缓存子代理的检索路径而不是结论，失效判据由模型自写的校验命令给出）
- [聊天消息的递归压缩](chat-condense.md)（未实现；把 `condense_ops` 那套延伸到聊天消息，节点自带 cid 因而可被再次压缩；记录已拍定项、未定项，以及落地时会失效的现有 `WHY`）
- [私聊窗口的身份](window-identity.md)（已实现；`user_id` 是作者、`target_id` 是那条私聊，「谁发的」与「发到哪」不再共用一个字段）
- [自发消息回流](self-message-echo.md)（未实现；把「Bot 说过的话」的写入权威从 `get_msg` 回查换成 `message_sent` 回声，前置实测已完成，关卡与 `record_sent` 的摘除必须同一笔落）
- [异常、日志与 link 报错节流](errors-and-logging.md)（按严重程度分）与[日志分流：流的身份与出口](log-streams.md)（按流的身份分；两者正交）
- [消息表示与 at/reply 解析](message-representation.md)（调查；§2 列出的五个问题已按其中 A 方案的前半修掉四个半，本篇按节标注了现状，未做的是模型侧的结构化渲染）
- [权限演进](permissions.md)
- [多项目共存](multicore.md)
- [模型选择的宽松解析与在线模型列表](model-selection.md)（已实现；未登记的模型照常调用，`#models` 先问对端、取不到才用本地配置）
- [op 工具集与事件注入](op-toolbox.md)（未实现；op 专属工具集：把事件交给唯一事件循环的注入原语，以及「开一轮聊天」的续话原语——`.reboot chat` 是后者的第一个消费者）
- [窗口级 `#hint`](chat-hint.md)（已实现；给聊天窗口存一段可配置代码（本质是文本管理），聊天循环停止时求值并发送——用于显示已用上下文这类运行状态，也让用户看到循环停没停）

剩余提案实现后，应把已验证事实移入现行文档，并把这里改成简洁的决策记录；不要让提案与现行文档长期维护两份运行契约。
