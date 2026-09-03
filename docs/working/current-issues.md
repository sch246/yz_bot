# 当前维护队列

本文记录架构切换后仍未决定或需要真实运行观察的事项。当前事实以[交互模型](../interaction-model.md)、[运行架构](../architecture.md)和各功能文档为准。

## 需要运行观察

- 新入口首次由维护者启动后，观察 Module Import/Load 汇总、管理员初始化/恢复提示、link/capture 命中打印、LLM stream/工具调用、图片捕获缓存和 Ctrl+C 有序退出。
- `.setu` 的网络访问已延迟到调用时；link helper 对多页图片 URL 的旧改写语义尚未决定，本轮保持现状。

## 已确认的缺陷

以下两条在读代码时确认，并已实测复现；它们独立于[统一消息模型](proposals/message-model.md)，但那篇提案的实施依赖它们先修好。

### ~~`cq.unescape` 的替换顺序把实体还原了两次~~（已修）

`mods/text.py` 的 `replace_by_dic2` 原本按字典序遍历，于是 `cq.unescape` 先把 `&amp;` 还原成 `&`，再把得到的 `&#91;` 当成实体还原成 `[`。用户字面输入 `&#91;` 会被吞掉。已改为倒序遍历——「撤销 `replace_by_dic`」本来就是倒着走，`&amp;` 因此最后还原；`escape` 方向（先 `&`）本来就是对的，未动。

影响面曾覆盖所有把消息正文当源码读的入口：`.py`、`.link`、`.hs`、`.js` 等 13 处 `cq.unescape(body)`，以及 `chatlog` 的正文写入。

### ~~私聊窗口里 `is_self` 会把 Bot 自己的消息算成对端的~~（已修）

`mods/message.py:95` 让 Bot 自发的私聊消息带上对端的 `user_id`（群聊则带 Bot 自己的），而旧的 `history.is_self` 用顶层 `user_id` 判断「同一个人」，于是私聊里两者无法区分。已改为 `history.same_author`，按 `sender` 判定作者——那是两种窗口下都指向作者的字段。

实际影响过 `mods/op.py:45`、`mods/post.py:62` 的提醒节流和 `mods/cave.py:168` 的 `get_self_log`；其中只有后者会产生用户可见的错误内容（私聊 `.cave addn -<n>` 会把 Bot 的回复合并进回声洞）。缺陷是原生的而不是迁移引入的，取证见[统一消息模型](proposals/message-model.md)的第 4 步。

## 接受的现行约束与取舍

### LLM 以可信聊天范围为主要边界

LLM 工具可以执行共享 `.py` 环境和读写用户 storage。维护者通过手动选择可信群控制启用范围，当前不要求逐工具确认或沙箱。私聊不受群白名单限制；若需要额外加固，候选方案是在所有 LLM 入口统一要求触发者为 op，但这尚未决定或实现。

无上限工具循环和 `assign_tasks` 递归/预算当前由 `.reboot` 作为极端恢复手段；完整 traceback 发送给可信模型供应商则是为故障分析保留的有意行为。只有出现实际事故证明这些机制不够时，再增加最小限制。

## 已由 Mods 切换解决

以下旧缺陷不再属于维护队列：失败命令占住入口、`^C` 无法唤醒 `.py input()`、群聊 `.post` 被 `uNone` 覆盖、link action 异常继续成功后继、冷启动 pyload 名称需要下一条消息刷新，以及 `.py` 通过旧命令表成为主路由硬依赖。

新的架构设想进入[设计提案索引](proposals/README.md)，不要把本页扩张成长期愿望清单。
