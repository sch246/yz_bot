# 消息表示与 at/reply 解析（调查与设计空间）

本文是一次现状调查，不是已决定的方案。当前事实以[交互模型](../../interaction-model.md)、[运行架构](../../architecture.md)为准；本文里凡是标注「现状」的段落都可以直接对照源码验证，标注「设计空间」的段落尚未选择，需要维护者先纠正理解再细化。

调查起点是一句现场判断：at 和引用的识别方式很原始，而且改写了消息本体；这种干涉是人为构造出来的，也不利于模型理解和解析。下面先把这条路径完整摊开，再分开陈述「哪些是真实缺陷」「哪些形状其实在保护某个事实」「哪些还没定」。

> **本文写于「派生值改纯函数」落地之前，作为调查记录原样保留。** 那一轮取的是 §4 的 A 半条：`event["message"]` 恢复为只读原文，`_strip_reply` 删除，回复/正文/at 由 `mods.msgs` 的 `reply_cq()` / `body()` / `at_cq()` 现算；下面每一节都标了现在的状态。当前事实以[运行架构](../../architecture.md#唯一消息路由)为准。
>
> | 节 | 状态 |
> |---|---|
> | 2.1 唯一权威被就地改写 | **已解决**——不再有人改写 `message` |
> | 2.2 剥离只做了一半 | **已解决**——`body()` 连开头的 at 一起去掉 |
> | 2.3 两个死接口 | **已删除** |
> | 2.4 上下行不对称 | **已解决**——两个方向都是原文 |
> | 2.5 模型侧视图 | **解决一半**——模型现在看得见 reply，at 仍然只有号码 |
> | 5 待定问题 | 第 1、2 条已回答（入口与 link 条件都读 `body()`：去 reply、去开头 at、lstrip）；其余未定 |

## 1. 现状：一条消息从进入到被消费

```text
connect.recv_msg()                       原始 OneBot dict，message 是 CQ 字符串
  → bot.recv()                           心跳 / input_status 提前返回
    → bot._route()                       mods/bot.py:144
      ① context.set_current(event)
      ② chatlog.write(event)             mods/bot.py:150
           _message()                    mods/chatlog.py:151  按**原文**写入 chatlog 文件
           history.add_msg()             mods/chatlog.py:106   把**同一个 dict 对象**存进近期窗口
      ③ chat.eager_cache_images(event)
      ④ _strip_reply(event)              mods/bot.py:161  ← 就地改写 event["message"]
      ⑤ ^C / continuation / .命令 / ! / #! / link 依次判定，全部读改写后的 event["message"]
```

`_strip_reply`（mods/bot.py:34-44）做三件事：

```python
event["reply"] = None
match = _reply.match(value)          # ^(\[CQ:reply,[^\]]+\])([\S\s]*)
if match: event["reply"], value = match.groups()
event["message"] = value             # 本体被替换
event["at_cq"] = [...]               # 消息里所有 at CQ 的原始串
```

出站方向没有对称处理：`message._send_now` 发完消息后用 `get_msg` 取回自己那条，直接 `_chatlog_write`（mods/message.py:98），不经过 `_route`，因此不剥离。

实测（一次性脚本，非仓库内测试）：

```text
入  [CQ:reply,id=…][CQ:at,qq=…] 柚子，这个 [CQ:image,…] 怎么看
出  message = "[CQ:at,qq=…] 柚子，这个 [CQ:image,…] 怎么看"
    reply   = "[CQ:reply,id=…]"
    at_cq   = ["[CQ:at,qq=…]"]
```

## 2. 现状里可以直接指出的问题

### 2.1 唯一权威被就地改写，而它的两个投影已经分叉

chatlog 文件在 ② 写入，写的是原文；history 在 ② 存的是**同一个对象**，④ 之后它变成剥离版。于是同一条消息，chatlog 文件里带 `[CQ:reply,…]`，内存 history 里没有，而 LLM 上下文读的是 history（`chat.get_msgs` → `history.getlog`）。这直接违反了[设计原则](../../design-principles.md)里的「一个事实一个写入权威，允许多个有意投影」：这里不是有意投影，是同一份数据在被读过之后又被改。

**已解决。** 另有一个当时没看到的后果：`chatlog` 启动重建是从**文件**读回内存的，所以重启之后 history 里那些恢复出来的消息带着 reply CQ，实时收到的不带——同一个 Bot 的模型视图在一次重启前后不同。现在 `message` 谁都不改，重建结果与实时事件逐字相同。

### 2.2 剥离只做了一半，导致入口在「引用回复」时静默失效

`_reply` 只吃掉**开头第一个** reply CQ。客户端在引用回复时常常紧接着插入一个 at，于是剥离后的本体形如 `[CQ:at,qq=…] .cave add`。随后的判定全是前缀匹配：

- `value.rstrip() in ("^C","^c")`（mods/bot.py:165）→ 不成立，取消入口失效；
- `value.startswith(".")`（mods/bot.py:173）→ 不成立，点命令不执行，消息掉进 link；
- `value.startswith("!")` / `"#!"`（mods/bot.py:179、184）→ 同上；
- `chat.cond` 的 `value.startswith(f"{bot_name}，")`（mods/chat.py:399）→ 不成立，只能靠 `has_at` 兜住。

也就是说，「剥离 reply」这个动作要保护的正是「引用着也能发命令」，但它只解决了 reply 自己造成的那一半干扰。

**已解决。** `msgs.body()` 一开始继承了同一个边界，随后按本节改成一次算清楚：去开头 reply → `lstrip` → 去开头的 at（连续多个都去）→ `lstrip`。只去开头的，因此 `.op [CQ:at,…]` 这类把 at 当参数的命令不受影响。

当时考虑过给前缀分派单独一个 `plain()`，结论是不要：那会再造出两套正文规则，而 link 条件、延续式回答、单 CQ 判定要的其实是同一套语义——它们失效的原因和点命令失效的原因是同一个。代价是 `at_cq()` 不能再读 `body()`，否则刚去掉的开头 at 会从「谁 at 了我」里消失；它改读只去 reply 的内部投影 `_after_reply()`。

### 2.3 两个死接口

- `event["at_cq"]`：只在 mods/bot.py:41 写入、在 mods/message.py:193 清除，**全仓没有任何读取点**。
- `msgs.is_reply` 的 `"reply_cq" in msg` 分支（mods/msgs.py:66）：没有任何地方写 `reply_cq`。

它们是上一轮解析方式留下的残骸，现在只是让读代码的人以为存在一套 at 处理机制。

**已删除。** `is_reply` 现在是 `reply_cq(msg) is not None`；at 列表改由 `msgs.at_cq(msg)` 现算，`py.loc` 里补了 `at_cq` / `reply_cq` / `is_reply` / `msg_body` 供 link 与 `.py` 使用。

### 2.4 上下行不对称

用户消息进 history 前被剥离，Bot 自己的消息进 history 时保持原文。于是模型在同一段上下文里会看到两种格式：自己说过的话带 `[CQ:reply,id=…]`，别人说的话不带。`message.get_reply`（mods/message.py:210，并通过 `py.py` 暴露给 link 和 `.py`）依赖 `event["reply"]`，因此它只对「刚刚走过 `_route` 的那条消息」有效，对从 history 里翻出来的 Bot 自己的消息恒返回 `{}`。

**已解决。** 两个方向都保留原文，`get_reply` 改读 `msgs.reply_cq(event)`，因此对任何来源的事件都成立——实时的、history 里的、从聊天日志重建的。

### 2.5 模型侧拿到的是被削过一刀的原始协议串

`msg2chat`（mods/chat.py:121）给每条用户消息拼一段 `<metadata>`（user_id / name / time / message_id），正文交给 `msg_split`（mods/chat.py:105）——它只认 image CQ，其余 CQ 一律当纯文本原样塞给模型。结果是：

- **回复关系彻底丢失**。metadata 里明明有 `<message_id>`，模型完全有能力把「这条在回复那条」对上，但 reply 在入口就被剥掉了，且没有任何字段补回上下文。
- **at 只有号码**。模型看到 `[CQ:at,qq=1234…]`，要自己去别处找这个号是谁；而 `identity.getname` 本来就能给出名字，metadata 里也已经在用它。
- 其余结构（face、forward、file、json 卡片等）在模型眼里全是噪声字符串。

系统提示（mods/chat.py:212）只告诉模型「at 格式为 `[CQ:at,qq=…]`，reply 格式为 `[CQ:reply,id=…]`」，这是让模型直接在正文里写协议码——它的输出不经转义就发出去（`chat.get_handler` → `message.sendmsg`）。所以协议和文本共用同一个通道，是**双向**的，不只是输入侧。

**回复关系已解决，at 名字未做。** 正文不再被剥，模型直接收到 `[CQ:reply,id=…]`，系统提示补了一句说明这个 id 与上文各条 `<metadata>` 里的 `<message_id>` 对应，所以模型能在自己的上下文里把「这条在回复那条」对上。at 仍然只有号码：给 at 目标注上 `identity.getname` 的名字会让输入与输出格式不对称（提示同时要求模型**写出** `[CQ:at,qq=…]`），属于 §5 第 3、4 条，未定。

## 3. 这些形状原本在保护什么

在动手之前需要承认三件事，否则「去掉改写」会退化成 bug：

1. **剥离 reply 保护了「引用着也能触发」**。link 条件是设备上可编辑的正则/模板（`text.stc_get(condition)(cq.unescape(event["message"]), py.loc)`，mods/link/\_\_init\_\_.py:131），大量条件是 `^好耶$` 这种全匹配。一旦本体前面挂着 reply CQ，这些条件全部不命中。点命令同理。
2. **`event["reply"]` 是 live link 和 `.py` 的现有词汇**。`get_reply` 已经通过 `DYNAMIC_EXPORTS`（mods/py.py:88）暴露给设备上的可执行配置；`CQ_at`（mods/link/\_\_init\_\_.py:26）同样是 link 条件里在用的 at 匹配方式。改这两处属于用户可见变更。
3. **普通 dict + 字符串是刻意的**。[设计原则](../../design-principles.md)明确反对为 OneBot 事件建立 DTO 或 message class 层级。任何改进都应该以「普通函数 + 事件里多一个派生字段」为默认形态，而不是引入 Message 对象。

## 4. 设计空间（未选定）

### A. 不改协议表示，只增加派生视图

`event["message"]` 恢复为**只读原文**，另外提供一组纯函数（放在 `mods.cq` 或一个新的小模块里）：

```python
segments(event) -> list[dict]   # [{"type":"reply","data":{...}}, {"type":"text","text":"…"}, …]
reply_id(event) -> int | None
ats(event) -> list[int]
plain(event) -> str             # 去掉前导 reply/at 之后、供入口前缀判定用的文本
```

入口（`^C`、`.`、`!`、`#!`、`柚子，`）改读 `plain(event)`；link 条件读什么单独决定（见 §5）。模型侧由 `msg2chat` 把 segments 渲染成结构化文本，例如把 reply 变成 metadata 里的一行 `<reply_to>`、把 at 渲染成号码加名字。

- 收益：本体不再被改写，chatlog 与 history 自然一致；§2.2 的半截剥离变成「一次算清楚」；死字段可以删掉；模型第一次拿到回复关系。
- 代价：每个 `event["message"]` 的消费点都要判断该用原文还是 `plain`（当前有 `file.py:200`、`dir.py:41`、`msgs.is_cq/is_img`、`chatlog._message`、`link` 条件等）。这不是机械替换，要一处一处定。

**已取其前半，且 `plain()` 没有单独存在——`body()` 就是它。** 落地的判据是一句话：**解释**消息的地方读 `msgs.body()`，**存储和显示**消息的地方读原始 `message`；按此逐处定过的消费点是 bot 路由前缀、link 条件与四处输入收集、chat 的 `#` 过滤与唤醒判定（`has_at` 改读 `msgs.at_cq()`）、`py.match`、`py.chat_input`、`history._predicate`、翻页导航、cave 收集与确认、file/dir 的确认与单 CQ 取值、jrrp、cpp、edit。模型、chatlog、图片预缓存继续读原文，这是有意的。没做的是后半：`segments()` 与模型侧的结构化渲染仍然不存在，`msg_split` 依旧只认 image。

### B. 换用 OneBot 数组消息格式

让 NapCat 用 `messagePostFormat: array`，从协议源头拿到分段结构，`cq.find_all` 这类正则解析可以整体退休。

- 收益：解析不再是「在字符串里找方括号」，转义问题（`cq.escape/escape2` 两套）也随之收敛。
- 代价：全仓假设 `message` 是 `str`；chatlog 是人可读文本文件、link 条件是字符串正则、模型输出还是字符串——三者都需要一个稳定的 `render(segments) -> str`，等于同时维护 parse 和 render 两个方向。而且这是设备侧配置变更，会同时影响正在跑的生产实例。

### C. 只做修补

`_strip_reply` 连同紧随的 at 一起吃掉、Bot 自己的消息也走同一次规范化、删掉 `at_cq` 和 `reply_cq`。

- 收益：改动最小，§2.2、§2.3、§2.4 当场消失。
- 代价：§2.1（就地改写）和 §2.5（模型视图）原样保留，也就是这次调查真正想解决的两件事一件没动。

三条路不是互斥的：C 可以作为 A 的第一步；B 只有在 A 的派生函数已经把「谁需要结构、谁需要原文」问清楚之后才谈得上代价可控。

## 5. 需要维护者先定的问题

1. **入口判定以哪种文本为准**：原文、去掉全部前导 CQ、还是「纯文本片段拼接」？三者对 `[CQ:at] .cmd`、`.cmd [CQ:image]`、纯图片消息的结果不同。
2. **link 条件面向哪一种文本**：它是设备上可编辑的持久配置，语义变更对用户可见，且现有条件里已经有直接匹配 `[CQ:…]` 结构的写法（`CQ`、`CQ_at`）。是保持原文、还是同时提供两个变量名？
3. **模型输入渲染到什么程度**：只补回 reply 和 at 名字，还是把整条消息都结构化？后者会让模型看不到真实 CQ 串，而它的输出又必须写 CQ 串。
4. **模型输出是否也要换通道**：现在模型直接写 `[CQ:at,qq=…]` 且不转义。若输入改成结构化，输入输出格式就不再对称，需要在系统提示里明确。
5. **chatlog 文件格式是否跟着变**：历史文件不可回溯改写，一旦改格式就会出现分界线。
6. **是否走 B**（数组格式）：这需要改设备上的 NapCat 配置，属于部署侧决定。

## 6. 理解检查（按[设计原则](../../design-principles.md)要求）

1. **维护者在优化什么**：少量透明、可现场组合的机制换取大的表达面，出错时人能从聊天、源码、文件、日志观察并恢复。所以这里的目标不是「加一个消息解析层」，而是让同一条消息在入口、link、chatlog、模型四处看到的东西可以互相解释。
2. **必须保留的用户可见行为**：引用着发点命令要能触发；`^C` 必须始终能抢在阻塞会话前面；link 的顺序与首个命中消费；群聊共享上下文；`.py`/link 现有词汇（`get_reply`、`CQ_at`、`at2qq`）。
3. **值得保留形态的小机制**：OneBot 事件保持普通 dict；`run(body)` 拿到的是命令名之后的原始 body；`msg_split` 用「纯函数把消息切成模型 content」这种形状（问题在于它只认 image，不在于它是函数）。
4. **明确属于历史偶然或缺陷的**：`at_cq`、`reply_cq` 两个死接口；chatlog 写入之后再改同一个对象；出入方向不对称；只剥离开头一个 reply。
5. **仍未决定的**：§5 全部六条。
6. **本项目里不能默认采用的惯例**：Message/Segment class 层级、统一 parser 中间件、把 `event["message"]` 换成对象、为「消息类型」建能力枚举。这些都必须先用上面的具体问题证明自己，而不是因为「解析应该有个层」。

## 7. 本轮已做与未做

已做：完整路径梳理、上述问题的源码定位、一次性脚本验证剥离行为与模型侧 content 形状。
未做：任何代码改动。§4 的三条路都会改到用户可见行为（入口判定、link 条件、模型视图），不适合在维护者确认方向前动手。
