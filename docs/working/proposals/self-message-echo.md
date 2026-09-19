# 自发消息回流：把「Bot 说过的话」的写入权威从回查换成回声

> **已实现（2026-09-19，§A+§B+§D 同一笔）。** 运行事实以[运行架构](../../architecture.md)与[部署与测试风险](../../runtime.md)为准；本文保留判断、被排除的做法和一条未决问题（§C 末尾）。
> 私聊窗口的身份约定见[私聊窗口的身份](window-identity.md)；关卡的由来见 `ce0e8b2`、`0a334b7` 两笔的提交说明。

## 落地的样子

`reportSelfMessage` 打开，Bot 自己发出去的每条消息作为 `post_type: "message_sent"` 回流；
`bot._route` 先 `chatlog.write(event)`（终端标签按 `post_type` 分），再按 `message_sent`
早退一个 `"self"` 标签，不派发。`message.record_sent` 连同它那次 `get_msg` 回查、以及
`mods/forward.py` 的补登记一起删掉；`chatlog.write` 里那个为容纳 `get_msg` 降级响应而存在的
`post_type is None` 分支也随之删掉（它的唯一生产者没有了）。

差分脚本（临时，仓库外）量过同一条私聊回声在改动前后的落账：旧代码 0 行、新代码 1 条记录，
群聊同理；`post_type: "message"` 的自注入事件两边都照常记录并越过关卡。

## 改之前的形状

`mods/message.record_sent` 是「Bot 说过的话进聊天记录和内存历史」的**唯一写入权威**：`send_msg` 发完拿到 `message_id`，再调一次 `get_msg` 回查，把响应写进 chatlog 与 `history`。合并转发不经过 `mods.message` 的发送队列，所以 `mods/forward.py` 自己补一次同样的调用。

与之配套，`_route` 开头按 `post_type == "message_sent"` 把自发消息**整段跳过**——不记录也不派发。`0a334b7` 把这道关卡从 `chatlog.write` 之后挪到之前，正是为了让「顺带挡住重复记账」那句话成立：`chatlog.write` 的判据收 `message_sent`，而 `history.add_msg` 不按 `message_id` 去重。那笔的差分脚本量过：旧顺序下 `_route` 会写两次记录（`[9, 9]`），新顺序一次都没有。

**`reportSelfMessage` 现在已经打开**（为做 §C 的实测）。所以自发消息此刻真的在进来，只是全被那道关卡丢掉了——这是安全的现状，不是巧合：关卡在写之前，所以没有双写。

## A. 判断：换成回流做权威，是正解

现成的洞：`record_sent` 的回查会失败（`get_msg` 非零 retcode 时 `_log.warning` 后 `return None`），此时 `_chatlog_write` 不执行——那条消息**发出去了、人看得见，而 chatlog 和 history 里都没有**。模型不记得自己说过。而且它是沉默的：警告进日志，模型这边什么都不知道。

三条支持理由：

1. **`record_sent` 存在的理由，回流恰好完成了它。** 它自己的 WHY 写着「它原先只在 `_send_now` 里，于是只有走 send_msg 的消息才被记下来……用别的 action 发出去的东西就变成一段无痕」——它是在**手工枚举发送路径**，而 `forward.py` 那次补登记就是这个枚举没做完的证据。回流对任何 action 自动生效（§C 第 1 条已证：合并转发也回流）。
2. **回声与实时入站同形，所以省掉一整步。** `record_sent` 现在要给回查结果补 `target_id`（`get_msg` 带作者、不带私聊窗口），换成回流之后连这一步都不需要——回声两个方向都填 `target_id`，和真人发来的那条一模一样。**换权威不需要「把回声改写成入站形状」。**
3. 每次发送少一次 API 往返。

代价一条：**延迟约一秒**（§C 第 4 条）。`record_sent` 在发送线程里同步写完；回流要走网络与 ingress 队列。两者都已经异步于聊天轮，所以「下一轮 `build_context` 看不到自己上一句」这个**类别**今天就存在，回流只是把窗口拉宽到约一秒。连续轮之间的间隔可能短于它。

## B. `record_sent` 整个删掉

它今天干两件事，两件都没有了：

- **写记录**：归回流。
- **拿 `message_id` 给调用方**：两个调用点本来就各自从发送结果里拿到了——`_send_now` 读 `result["data"]["message_id"]`，`forward.py` 读 `(result.get("data") or {}).get("message_id")` 之后才把它传进来。

而且它的**返回值没有任何消费者**（两个调用点都忽略）。所以 `record_sent` 连同那次 `get_msg` 一起删，两个调用点一起删。`chatlog.write` 里那个 `post_type is None and "message" in msg and "message_id" in msg` 分支是为容纳 `get_msg` 降级响应而存在的，它的唯一生产者也随之消失。

## C. 实测结果（草籽 2026-09-19，tcpdump 抓 5701；已完成）

| 问题 | 结果 |
|---|---|
| `send_forward_msg` 回不回流 | **回流**，`message` 就是完整的 `com.tencent.multimsg` 那条 `CQ:json` |
| 回流事件的 `post_type` | `message_sent`，另带 `message_sent_type: "self"` |
| 私聊回流事件的窗口 | 顶层 `user_id` 是**作者**（Bot 自己），私聊由 `target_id` 给出，**两个方向都填**；对端发来的那条里 `user_id` 与 `target_id` 相同——所以 `target_id` 不是收件人，是「哪条私聊」的标识 |
| 延迟 | 约一秒 |

第 3 条是新事实，它推翻了「私聊里顶层 `user_id` 是窗口对端」这条旧约定，已由 `000ca9c` 整条撤掉（见[私聊窗口的身份](window-identity.md)）。

**一个由实测冒出来的新问题，留给草籽：** `message_sent_type` 这个字段的存在说明 NapCat 区分了自发消息的**种类**，而实测只见过 `"self"`。如果同一个 QQ 号在别的客户端（比如手机）上说话，那条消息大概也会回流。它确实是「这个账号说的话」，但**不是模型说的**——换权威之后，模型会把它当成自己上一轮的发言读进上下文。这不一定是错的（Bot 的号说过的话就是它说过的话），但要有人明确决定，而不是默认继承。

## D. 关卡：改成「记录、不派发」

目标：关卡挪到 `chatlog.write` **之后**，early return 一个独立标签。一个 return 同时挡住 `^C`、waiter、`.`、`!`、`#!`、`link.dispatch`（`#` 子命令在 `capture_chat` 的 `cond()` 里，靠不派发自然挡住）。

**它必须和 §B 同一笔落**，否则就是把 `0a334b7` 刚用差分脚本证掉的双写原样装回来。§C 出来之后这个条件已经满足：回流覆盖两条发送路径，所以 `record_sent` 可以一次删干净，不存在「一半回流、一半回查」的中间态。

两条 WHY（已写在 `bot._route` 的关卡处）：

- **关卡的命题要换，不只是换位置。** 旧的「整段跳过」把两件事合并了：不记录，和不授权。新形状把它们分开，所以 WHY 该写成分开后的不变量——**Bot 说的话是记录的来源，永远不是指令的来源**。这句话能回答以后新增的派发路径该放在关卡哪一侧，而「自己的消息不派发」不能。
- **判据只能是 `post_type`。** `mods/tools/op.py` 的 `_event` 伪造的是 `post_type: "message"`，它**必须**被派发；回流事件只差这一个字段（两者的 `sender.user_id` 都是 Bot）。换成「作者是不是 Bot」会连 `send_command` 一起挡掉。这条已经作为 WHY 写在 `_event` 里——它今天就承重。

也不要改判据去读 `message_sent_type`：那个字段是 NapCat 的扩展，而关卡要挡的是「这是我自己发出去的」，与种类无关。种类的问题归 §C 末尾那条。

一条小的，漏了会让人排查时困惑：**终端回显的标签**。`_route` 写完打的是【收到消息】，`message._chatlog_write` 打的是【发送消息】。权威换到回流之后，Bot 自己的消息会由 `_route` 打成【收到消息】，标签要跟着 `post_type` 分。

## 剩下的

1. ~~关卡挪位 + 删掉 `record_sent` 与它的两个调用点 + 终端标签，同一笔。~~ 已落。核心模块，**需重启才生效**。
2. 观察一段：私聊两个方向、群聊、合并转发各自在 chatlog 里只出现一次，且窗口正确。差分脚本证的是 `bot._route` 这一层，真发送路径只有真跑一遍才知道。
3. §C 末尾那条（别的客户端发的话算不算「模型说过的」）单独决定。
