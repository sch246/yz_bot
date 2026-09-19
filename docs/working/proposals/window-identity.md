# 私聊窗口的身份：`user_id` 是作者，`target_id` 是那条私聊

**已实现（2026-09-19）。** 记录这条约定的由来、实测证据、改动面，以及被删掉的中间做法。

## 问题

一条私聊事件里「谁发的」和「发到哪条私聊」是两件事，而过去它们共用一个字段：顶层
`user_id` 群聊里是作者、私聊里是**窗口对端**。作者只好另写 `sender.user_id`，而
「把 Bot 自己那条的 `user_id` 改写成对端」这一手要在**每个入口**都记得做：

- `message.record_sent`：`get_msg` 回查回来的事件不带窗口，于是拿发送目的地去顶。
- `chatlog._message_record`：从档案重建时再折回去（`user_id = 作者 if 群聊 else 窗口`）。
- `op._event` / `cmds._event` / `recvmsg`：伪造入站事件时手工摆位，作者与窗口一处一个。
- 消费端因此分成两派：问「谁发的」读 `sender`，问「哪个窗口」读 `user_id`；判错不会报错，
  只会把话记到错的窗口、或把回复发给自己。

代价不是抽象的美观问题。`chatlog/private/236288772/`（Bot 自己当成私聊对端）和
`later_list/users/236288772.json` 就是这么来的——注入事件把作者当成了窗口。

## 实测（2026-09-19，抓 5701 上的真实入站）

| 方向 | `post_type` | `user_id` | `target_id` | `sender.user_id` |
|---|---|---|---|---|
| 对端发来 | `message` | 980001119（对端 = 作者） | 980001119 | 980001119 |
| 自己发出（`reportSelfMessage` 的回声） | `message_sent` | 236288772（Bot = 作者） | 980001119 | 236288772 |
| `get_msg` 回查自己那条 | `message_sent` | 236288772 | **没有这个键** | 236288772 |

三点结论：

1. `target_id` 是「哪条私聊」——两个方向都填、与方向无关，**不是收件人**（对端发来时它
   等于 `user_id`，收件人其实是 Bot）。
2. `user_id` 两个方向都是作者，所以它不再需要与 `sender` 分工。
3. 回查是唯一丢窗口的一路，而它**知道**目的地（发送时那个参数），补上就行。

## 约定

- **`user_id` 是作者**，两种窗口、实时与重建一致。`history.author` 保留 `sender` 优先，
  只为兼底 v0 私聊行——那里作者只能按名字猜，猜测与名字一起写在 `sender` 上。
- **私聊窗口是 `target_id`**，群聊窗口是 `group_id`；`history.window` 是唯一读法。
- `target_id` 缺失时 `window()` 返回 `None`：窗口未知好过猜一个错的。需要产出键的几处
  （`later` / `todo` / `chat.getchatstorage` / `mcversion` 的订阅）保留 `target_id or user_id`
  兜底，因为**改动之前**存进文件的任务与订阅里，窗口恰恰写在 `user_id` 上。

## 改动面

读窗口：`history.window`、`message.target`、`message.sendmsg`、`context.interaction_key`、
`chat.getchatstorage`、`chatlog.search_current`、`chatlog` 的私聊目录、`later`、`todo`、
`mcversion`、`tools/op._resolve_window`、`tools/cmds._resolve_window`、`op.require_op` 的提醒目的地。

写事件：`record_sent` 补 `target_id`（作者不动）、`_message_record` 分开存两半、
`op._event` / `cmds._event` 摆对位置。

## 不改的

- **磁盘格式**：行头本来就是作者（v1 起带号码），窗口本来就是路径。改的只是内存里的摆法，
  旧文件不必迁移。
- **notice 一族**（撤回 / 戳一戳 / 文件 / 好友请求）：那里的 `user_id` 是「这条通知关于谁」，
  消费者只读格式化器自己写下的 id。它们是另一族，不在这条约定内。
- **出站**：`_send_now` 的参数本来就是目的地。

## 被删掉的中间做法

`record_sent` 的 `sent_event["user_id"] = 对端`（回查没有窗口，就拿目的地顶掉作者），
以及 `_message_record` 的 `record["user_id"] = sender_id if kind == "group" else target`
（重建时把窗口写回 `user_id`）。两句都是同一个妥协的两半，现在一起消失。

## 与「自发消息回流」的关系

回声换权威（把「Bot 说过的话」的来源从 `get_msg` 回查换成 `message_sent` 回声）此后不再
牵动身份约定：回声不必再被改写成入站形状，它与实时入站同形。见[自发消息回流](self-message-echo.md)——
它已于同日落地，本篇提到的 `record_sent` 连同那次回查一起删掉了，所以上面那份「写事件」
清单里它那一项现在是历史。
