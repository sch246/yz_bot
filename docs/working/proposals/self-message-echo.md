# 自发消息回流：把「Bot 说过的话」的写入权威从回查换成回声

> 状态：未实现的提案，**阻塞在一项实测**（见 §C）。本文记录判断与目标形状，不描述当前运行行为。
> 当前事实以[运行架构](../../architecture.md)为准；关卡现状见 `mods/bot.py` 的 `_route` 与 `ce0e8b2`、`0a334b7` 两笔的提交说明。

## 今天的形状

`mods/message.record_sent` 是「Bot 说过的话进聊天记录和内存历史」的**唯一写入权威**：`send_msg` 发完拿到 `message_id`，再调一次 `get_msg` 回查，把响应写进 chatlog 与 `history`。合并转发不经过 `mods.message` 的发送队列，所以 `mods/forward.py` 自己补一次同样的调用。

与之配套，`_route` 开头按 `post_type == "message_sent"` 把自发消息**整段跳过**——不记录也不派发。`0a334b7` 把这道关卡从 `chatlog.write` 之后挪到之前，正是为了让「顺带挡住重复记账」那句话成立：`chatlog.write` 的判据收 `message_sent`，而 `history.add_msg` 不按 `message_id` 去重。那笔的差分脚本量过：旧顺序下 `_route` 会写两次记录（`[9, 9]`），新顺序一次都没有。

所以两个权威是**互斥**的，而这正是本篇要换的那一个。

## A. 判断：换成回流做权威，是正解

现成的洞：`record_sent` 的回查会失败（`get_msg` 非零 retcode 时 `_log.warning` 后 `return None`），此时 `_chatlog_write` 不执行——那条消息**发出去了、人看得见，而 chatlog 和 history 里都没有**。模型不记得自己说过。而且它是沉默的：警告进日志，模型这边什么都不知道，下一轮就是「我记得我要说，但不记得我说了」。

三条支持理由：

1. **`record_sent` 存在的理由，回流恰好完成了它。** 它自己的 WHY 写着「它原先只在 `_send_now` 里，于是只有走 send_msg 的消息才被记下来……用别的 action 发出去的东西就变成一段无痕」——它是在**手工枚举发送路径**，而 `forward.py` 那次补登记就是这个枚举没做完的证据。回流对任何 action 自动生效。
2. **回流的保真度更高。** `chatlog.write` 里那个 `post_type is None and "message" in msg and "message_id" in msg` 分支，注释说明是为 `get_msg` 响应准备的（它们「do not consistently include post_type」）。也就是回查产出的是**降级事件**，那个分支就是为容纳降级而存在的；回流是真正的 OneBot 事件。
3. 每次发送少一次 API 往返。

代价两条：

- **顺序。** `record_sent` 在发送线程里同步写完；回流要走网络与 ingress 队列。两者都已经异步于聊天轮，所以「下一轮 `build_context` 看不到自己上一句」这个**类别**今天就存在，回流只是换了窗口宽度。不是新问题，但要量。
- **过渡期只能有一个权威。** 两边都写就是双写，而去重走不通：`history` 没有 `message_id` 索引（`remove_message` 是线性扫），chatlog 是 append-only 文本，去重要读回来。所以必然是单权威、**按发送 API 划线**，而线画在哪由 §C 决定。

## B. `record_sent` 退化成什么

它今天干两件事，分开看：

- **写记录**：交给回流。
- **拿 `message_id` 给调用方**：`_send_now` 本来就从 `send_msg` 的 result 里直接取，不依赖它。

而且它的**返回值没有任何消费者**——`message.py` 与 `forward.py` 两个调用点都忽略。所以若 §C 判定回流覆盖合并转发，`record_sent` 连同那次 `get_msg` 一起删；若不覆盖，它缩成「forward 专用」，并且那个调用点必须写明它为什么是例外。

## C. 阻塞项：实测（要测到确定，测一次不算）

1. **`send_forward_msg` 的消息回不回流。** 这条决定 §B 的两个分支。
2. **回流事件的 `post_type` 是不是 `message_sent`。** 关卡判据压在它上面。
3. **私聊回流事件的 `user_id` 指谁。** `record_sent` 今天有一段专门修它（`if group_id is None: sent_event["user_id"] = user_id`），因为私聊窗口的顶层 `user_id` 是**对端**。回流若不守这个约定，`history.window()` 会把 Bot 的私聊消息归到错窗口——而这个错是静默的。**这条比第 1 条更容易咬人。**
4. 延迟量级（§A 的第一条代价）。

最怕的是 NapCat **有时候**报合并转发：那样双写会间歇出现，而去重走不通。

## D. 关卡的目标形状，以及为什么它不能先落

目标：从「整段跳过」变回「记录、不派发」——关卡挪到 `chatlog.write` **之后**，early return 一个独立标签。一个 return 同时挡住 `^C`、waiter、`.`、`!`、`#!`、`link.dispatch`（`#` 子命令在 `capture_chat` 的 `cond()` 里，靠不派发自然挡住）。

**但它不能独立落。** 把关卡挪回 `chatlog.write` 之后而 `record_sent` 仍在写，就是把 `0a334b7` 刚用差分脚本证掉的双写**原样装回来**；而 `record_sent` 的两个调用点里，forward 那个压在 §C 上。chatlog 是生产数据（见 [AGENTS.md](../../../AGENTS.md)），双写进去不是便宜的错误。

所以唯一正确的 D 是 **D + A + 配置开关同一笔落**，前置是 §C 的第 1、2、3 条。

落的时候还有两条 WHY 要写：

- **关卡的命题要换，不只是换位置。** 旧的「整段跳过」把两件事合并了：不记录，和不授权。新形状把它们分开，所以 WHY 该写成分开后的不变量——**Bot 说的话是记录的来源，永远不是指令的来源**。这句话能回答以后新增的派发路径该放在关卡哪一侧，而「自己的消息不派发」不能。
- **判据只能是 `post_type`。** `mods/tools/op.py` 的 `_event` 伪造的是 `post_type: "message"` + `sender.user_id = bot_id`，它**必须**被派发；回流事件只差 `post_type` 这一个字段。换成「作者是不是 Bot」会连 `send_command` 一起挡掉。这条已经作为 WHY 写进 `_event`（它今天就承重）。

一条小的，漏了会让人排查时困惑：终端回显的标签。`_route` 写完打的是【收到消息】，`message._chatlog_write` 打的是【发送消息】。权威换到回流之后，Bot 自己的消息会由 `_route` 打成【收到消息】，标签要跟着 `post_type` 分。

## 顺序

1. §C 的实测（阻塞）。
2. 关卡挪位 + 摘掉对应的 `record_sent` 调用点 + 打开 `reportSelfMessage`，同一笔。
3. 终端标签。

`_event` 的那条 `post_type` WHY 独立于以上，已先行落地——它保护的是**今天**就存在的事实：关卡现在就按 `post_type` 判，改动 `_event` 的那个字段会让自注入命令静默失效。
