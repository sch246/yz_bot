# 当前维护队列

本文记录架构切换后仍未决定或需要真实运行观察的事项。当前事实以[交互模型](../interaction-model.md)、[运行架构](../architecture.md)和各功能文档为准。

## 需要运行观察

- 新入口首次由维护者启动后，观察 Module Import/Load 汇总、管理员初始化/恢复提示、link/capture 命中打印、LLM stream/工具调用、图片捕获缓存和 Ctrl+C 有序退出。
- `mods.server` 的协议和所有权已经迁移；具体设备地址与私钥路径只应来自忽略的 `data/device/server.json`。若设备配置缺失，依赖 `%` 反应的首次调用会明确失败，不应回退到源码常量。
- `.setu` 的网络访问已延迟到调用时；link helper 对多页图片 URL 的旧改写语义尚未决定，本轮保持现状。

## 已确认的缺陷

以下两条在读代码时确认，并已实测复现；它们独立于[统一消息模型](proposals/message-model.md)，但那篇提案的实施依赖它们先修好。

### `cq.unescape` 的替换顺序把实体还原了两次

`mods/cq.py:30` 按 `_inside` 的字典序替换，`&amp;` 排在最前。用户字面输入 `&#91;` 时 OneBot 传来 `&amp;#91;`，先被还原成 `&#91;`，再被当成实体还原成 `[`。`&amp;` 必须最后替换；`escape` 方向的顺序（先 `&`）是对的，不用动。

影响面是所有把消息正文当源码读的入口：`.py`、`.link`、`.hs`、`.js` 等 13 处 `cq.unescape(body)`，以及 `chatlog` 的正文写入。

### 私聊窗口里 `is_self` 会把 Bot 自己的消息算成对端的

`mods/message.py:95` 让 Bot 自发的私聊消息带上对端的 `user_id`（群聊则是 `mods/message.py:97`，带 Bot 自己的），而 `history.is_self`（`mods/history.py:82`）用顶层 `user_id` 判断「同一个人」。于是在私聊里，两者无法区分。

`mods/op.py:45`、`mods/post.py:62` 的提醒节流和 `mods/cave.py:168` 的 `get_self_log` 都建在这个谓词上。`chat.msg2chat`（`mods/chat.py:122`）读的是 `sender.user_id`，是对的——同一个问题在仓库里已经有两套读法。修法与窗口/发送者的拆分是同一件事，见提案的第 4 步。

## 接受的现行约束与取舍

### LLM 以可信聊天范围为主要边界

LLM 工具可以执行共享 `.py` 环境和读写用户 storage。维护者通过手动选择可信群控制启用范围，当前不要求逐工具确认或沙箱。私聊不受群白名单限制；若需要额外加固，候选方案是在所有 LLM 入口统一要求触发者为 op，但这尚未决定或实现。

无上限工具循环和 `assign_tasks` 递归/预算当前由 `.reboot` 作为极端恢复手段；完整 traceback 发送给可信模型供应商则是为故障分析保留的有意行为。只有出现实际事故证明这些机制不够时，再增加最小限制。

## 已由 Mods 切换解决

以下旧缺陷不再属于维护队列：失败命令占住入口、`^C` 无法唤醒 `.py input()`、群聊 `.post` 被 `uNone` 覆盖、link action 异常继续成功后继、冷启动 pyload 名称需要下一条消息刷新，以及 `.py` 通过旧命令表成为主路由硬依赖。

新的架构设想进入[设计提案索引](proposals/README.md)，不要把本页扩张成长期愿望清单。
