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

**续修（2026-09-17）：同一处混淆还剩两个读者，已一并改走 `history.author`。**

- `op.is_op(event)`——op 门的判据。传事件时原来读顶层 `user_id`，私聊里那是窗口对端。
- `chat._usage_entry()`——LLM 费用的归属，docstring 本来就写着 "the acting user's"，原来也读顶层 `user_id`。

两者的用户可见变化都**只在私聊、且只在 Bot 自己是作者时**生效（群聊两个字段本来就相同）：前者让「以 Bot 自己的身份在当前窗口注入一条命令」能过 op 门，后者让那一轮的费用记到 Bot 自己而不是窗口对端。`history.author` 因此成为「谁发的」这个问题在仓库里的唯一读法。

**再续修（2026-09-19）：那条「私聊里 `user_id` 是窗口对端」的约定本身被撤掉了。** 抓 5701 上的真实入站看到：`user_id` 两个方向都是作者，私聊「是哪一条」由 NapCat 的扩展字段 `target_id` 给出、与方向无关；两件事因此不再共用一个字段，`later`/`todo`/`chattop`/`cave` 那批读者也不必再分两派。取舍、实测与完整改动面见[私聊窗口的身份](proposals/window-identity.md)。

`message.recvmsg()` 仍把作者与窗口写成一个值（`sender_id` 同时写进两处），所以它表达不了「作者 ≠ 窗口」——op 工具集按[提案](../proposals/op-toolbox.md)是手工构造事件投 `connect._events`，不走它。

## 接受的现行约束与取舍

### LLM 以可信聊天范围为主要边界

LLM 工具可以执行共享 `.py` 环境和读写用户 storage。维护者通过手动选择可信群控制启用范围，当前不要求逐工具确认或沙箱。私聊不受群白名单限制；若需要额外加固，候选方案是在所有 LLM 入口统一要求触发者为 op，但这尚未决定或实现。

无上限工具循环和 `assign_tasks` 递归/预算当前由 `.reboot` 作为极端恢复手段；完整 traceback 发送给可信模型供应商则是为故障分析保留的有意行为。

**「等实际事故再收紧」的条件已经触发。** 2026-09-17 12:10，一次 `exec_code` 在 `cwd="/"` 下跑 grep 卡死：协作式 `^C`（只在子请求起点和每个 chunk 有检查点）够不到同步执行的工具调用，按不动；卡住的工具让**整个窗口失能**，后续消息只排队、不发言；最后靠 `.reboot` 恢复。这既是一起「实际事故」的实例，也暴露了恢复粒度——`.reboot` 重启的是整个进程，而不是卡住的那一次调用。是否由此增加最小限制（工具超时、`^C` 真去杀子进程、`pending_calls` 循环每次调用前查 `should_stop`）尚未决定。

## 已由 Mods 切换解决

以下旧缺陷不再属于维护队列：失败命令占住入口、`^C` 无法唤醒 `.py input()`、群聊 `.post` 被 `uNone` 覆盖、link action 异常继续成功后继、冷启动 pyload 名称需要下一条消息刷新，以及 `.py` 通过旧命令表成为主路由硬依赖。

新的架构设想进入[设计提案索引](proposals/README.md)，不要把本页扩张成长期愿望清单。
