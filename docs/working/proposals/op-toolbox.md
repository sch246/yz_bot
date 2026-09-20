# op 工具集与事件注入

> 状态：**决定一（事件注入）与决定二（续话不造假消息）仍在运行；决定三（按轮门控）
> 已被 2026-09-20 的 [Bot 固定权限裁决](permissions.md)取代**。
> 当前代码见 `mods/tools/op.py` 与 `mods/reboot.py::resume_chat`。
>
> 最近更新：2026-09-17
>
> 来源：草籽 2026-09-17 12:39 提出「写一个 op 工具集，只有 op 发起的聊天才能调用，里面要有
> 异步给自己发消息的功能，这样能利用起命令系统」；12:46 补充「用聊天消息触发不优雅」。同日 12:34
> 用 `.reboot` 实测了本文的核心原语（投事件进队列让主线程处理）。

本文记录两条相关能力的共同原语与取舍：**op 专属工具集**（在 op 发起的聊天里，让模型能调用 Bot
自己的命令系统），以及**重启后续话**（`.reboot` 之后自动开一轮聊天）。两者共用同一批机制，所以写在一起。

## 背景

现在「以 Bot 的身份执行」已经有几个入口：`.py` 环境（含 `recvmsg`）、link 的 `inline` 动作、
`!` shell 命令。它们的信任边界一致——都只对 op 开放（见 [运行架构](../../runtime.md) 对 `recvmsg`
的定性：「分量等同于以被伪造者的身份执行」）。

但模型在自己的工具循环里**没有**等价能力。想「给自己发一条命令」「重启后接着聊」，只能落到
`exec_code` 里手写 `recvmsg(...)`，而且踩两个已经实测到的坑：

- **`recvmsg` 在调用者线程里同步跑 `bot.recv`。** `.reboot` 会 `raise SystemExit(233)`、`.shutdown`
  会 `raise SystemExit(0)`；这两个异常打死的是**调用者线程**，主线程收不到，进程既不重启也不退出。
  （12:34 实测：在 link 的 `_dispatch` 线程里 `recvmsg(".reboot")` 无效，改成投事件进队列才成功。）
- **同步执行阻塞工具调用线程。** 一个慢命令（12:10 那条 `cwd="/"` 的 grep）会让该窗口失能，
  后续消息只排队不发言——与 [当前问题](../current-issues.md) 里记的是同一个病。

所以正确的原语不是「调 `recvmsg`」，而是「**把事件交给唯一的那条事件循环**」。

## 决定一：事件注入 = 投进 `connect._events`

Bot 只有一条事件循环：主线程 `bot.run` → `connect.recv_msg()` → `connect._events.get()`。
`connect.py` 的注释已经把它的性质写死：「队列在这里不是缓冲设计，是转接头」。注入就是

```python
connect._events.put(event)   # event 形状与真实入站事件一致
```

主线程随后按**真实路由**处理它（`_route` → 命令 / link / 聊天），注入者立刻返回、不阻塞。
好处正是背景里那两个坑的解药：

- `.reboot` / `.shutdown` 的 `SystemExit` 落在**主线程**，由 `main.py` / `run.py` 正常接管；
- 注入者不阻塞（但被注入的命令仍会占住主线程跑完，与真人触发一致——这是已知代价，见「未决」）。

事件形状沿用 12:34 验证过的那一份：`post_type=message`、`message_type`、`sub_type`、`user_id`、
`message`/`raw_message`、`message_id`、`self_id`、`sender`。

## 决定二：续话不开「假消息」

草籽 12:46：把「重启后续话」做成*造一条* `柚子，继续` 的入站消息、靠 link 的 `cond()` 命中唤醒词，
不优雅。它依赖一串巧合（唤醒词前缀、`chat_groups` 白名单、`capture` 的挂载点），而那条消息在语义上
并不存在。更直接的形状是**「在某个窗口开一轮聊天，带一句种子」**：

- 目标是（群，群号）或（私聊，QQ 号）—— 也就是 `history.window(event)` 的键；
- 直接走 `context.begin_turn(window)` → `chat._run_chat(...)`，种子作为顶层 `_activate_chat` 的
  `messages` 参数传进去。这正是 `.chat` 命令已有的形状。

于是「续话」是**开一轮**，不是**假造一条入站消息**。`.reboot chat [文本]` 只负责把种子交给这条路，
不再伪造 QQ 消息。注入原语（决定一）留给「执行命令」，续话走「开一轮聊天」，两者不再混用。

## 决定三：门控按窗口、按轮，不在 registry 层

工具目录由 `tools._render_context` 从 `registry.modules` 渲染，那是**进程全局**的一份；而 op 门控是
**按窗口、按轮**的——同一个群窗口，这轮是 op 触发、下轮可能是普通成员。两者维度不同，所以：

- 在 `chat.init_chat`（那里拿得到 `context.current()` 与 `op.is_op`）**按当轮触发者过滤**要呈现的模块；
- 在 `SessionBinding.load` 与**工具执行时各再校验一次**，防止模型绕过目录、直接按名字 `load` 进来。

**不能**靠「从 registry 删模块」实现门控——那会污染所有窗口（含非 op 的）。

## 防回环

注入的事件会重新进路由，可能再次触发聊天 → 再调工具 → 再注入。两种做法二选一或并用：

- 给注入产生的事件打标记（例如 `event["_injected"] = True`），聊天触发对带标记的事件**只记录、不自动再续**；
- 或让每次注入调用显式声明「不再触发」。

`.reboot chat` 的续话同样要防：模型续话时若又决定 `.reboot chat`，就是无限重启。候选做法是
注入前先清掉 resume 状态、并让注入事件不参与「是否触发」的判定。

## 接口草案（名字待定）

`mods/tools/op.py`，只在「当轮触发者是 op」时呈现与可加载：

- `send_command(text, target=None)` —— 把 `text`（如 `.chattop`、`!ls`、`#ops`）作为一条事件投进
  `connect._events`，以 Bot 身份执行；`target` 缺省为当前窗口。
- `open_chat(window, seed)` —— 对某窗口开一轮聊天（决定二），供续话与「推动一轮」使用。
- `send_message(text, target)` —— 往指定窗口**说话**（走 `message.send`，不是注入）。这与 `send_command`
  的区别是「说话」与「执行」的区别，不该合并。

## 被排除的做法

- **直调 `message.recvmsg`**：线程错位（`SystemExit` 打不到主线程）与阻塞两个问题，见背景。
- **用「假入站消息 + link 唤醒词」触发续话**：草籽 12:46；依赖一串巧合，语义不实。
- **从 registry 删除模块实现门控**：registry 是进程全局的，会把 op 能力泄漏到所有窗口。
- **在 registry 层做 op 门控**：registry 不知道「窗口」和「当轮触发者」这两个维度。

## 与 `.reboot chat` 的关系

`.reboot chat` 是「开一轮聊天」这个原语的**第一个消费者**，op 工具集是同一原语的**第二个消费者**
（外加「事件注入」这个原语）。建议先落「开一轮聊天」（`.reboot chat` 用它，随时可验），
op 工具集随后接上；「主线程 + 不阻塞 + 防回环」这三条规矩只写一遍。

## 实现落地（2026-09-17）

- `mods/tools/op.py`：`OP_ONLY = True` + `send_command(text, target="")`。构造入站形状的事件、
  `connect._events.put(event)`，主线程按真实路由执行；工具本身立刻返回。
- 门控（决定三）：`mods/tools/__init__.py` 的 `op_tool_visible` / `_visible_catalog`，
  `ToolModule` 多一个 `op_only` 字段（由顶层 `OP_ONLY` 读出）；渲染目录、`SessionBinding.load`、
  工具内 `op.is_op` 三层。**没有**在 registry 层删模块。
- 续话（决定二）：`reboot.run` 把 `{"event": …, "resume": …}` 写进 greet 文件，`resume` 在**这一侧**
  用 `history.author(event) == identity.bot_id()` 算好；`reboot.resume_chat` 等 `mods.wait_booted()`
  之后 `context.set_current(event)` + `chat.chat()`。`mods` 新增 `wait_booted(timeout)`（一个在
  `boot()` 末尾 set 的 Event）——on_load 期间排在后面的模块还没加载完。
- 身份：顶层 `user_id` 留作**窗口**，作者写 `sender.user_id = identity.bot_id()`。配套把
  `op.is_op(event)` 的判据从顶层 `user_id` 改成 `history.author`（见
  [当前问题](../current-issues.md) 的「私聊 user_id 歧义」）。于是私聊里注入也能过 op 门，而
  「重启中/重启完成」照样回到原窗口。

## 未决问题 / 什么时候该推翻它

- ~~**注入事件的身份**~~（已定）：顶层 `user_id` 是窗口，作者是 `sender.user_id = bot_id`；
  `op.is_op` 读作者。计费随之归 Bot 自己（`chat._usage_entry` 也改读作者）。
- ~~**注入事件是否进历史**~~（已答）：会进。它以 `assistant` 角色出现在后续上下文里——它确实
  是 Bot 自己做的事。要它不进上下文，让 `text` 以 `#` 开头（全仓库通用的前缀约定）。
- **防回环没有自动拦截**：`resume` 只发生在"发起者是自己"这一条上，greet 文件读后即删，所以
  不会出现"重启→续话→又重启"的**自动**循环。但模型在续话那轮如果又决定 `.reboot`，那是一次
  新的、有意的重启，现在的口径是**允许**（每次都有「重启中/重启完成」可见，真成环是响的）。
  要收紧时的做法：注入事件已带 `_injected` 标记，可以据此只在"非注入来源"时 resume。
- **队列无上限**：`connect.py` 已注明事件堆在内存里是接受的代价；大量注入会不会把这条代价放大到需要加上限。
- **要不要 dry-run / 注入命名空间**：`docs/runtime.md` 指出现有 `5701` 入站没有测试命名空间，注入能力也会
  继承这个问题。
- 若某天 `recvmsg` 与 `connect._events.put` 的差别被抹平（例如 `recvmsg` 改成投队列），本文的「决定一」
  可以合并回 `recvmsg`；在那之前两者不可互换。
