# 运行架构

本文描述 2026-07-29 完成入口切换后的当前实现。用户可见语义以[交互模型](interaction-model.md)为准，部署和测试边界见[运行文档](runtime.md)。迁移时的取舍和旧源码去向保留在[架构切换记录](working/proposals/code-organization.md)，不再作为待实现目标。

## 唯一入口

正式路径只有一条：

```text
run.py
  → .venv/bin/python main.py
      → import mods          （只取得生命周期函数，不启动任何东西）
      → mods.boot()
          → Import 全部公开 mod
          → 按生命周期 Load
      → mods.bot.run()
      → mods.exit()
```

`run.py` 只负责参数、子进程监督、SIGINT 转发和完整重启。`main.py` 只准备运行目录、加载 `.env`、安装信号处理、启动 `mods` 并保证退出钩子执行。它们都不再充当业务名称出口。

## 平级 Module 与两阶段加载

`mods/` 下每个非下划线开头的 `name.py`，或含 `__init__.py` 的同名文件夹，都是公开 Module。文件和文件夹不能同名并存；文件夹内部的私有文件不单独参与扫描。

稳定依赖使用普通 import：

```python
from mods import storage
from mods.command import command
```

加载由入口显式调用的 `mods.boot()` 触发，import 本身没有副作用：loader 先按名称导入全部公开 Module，再按 `INFRA → FEATURE → LATE` 执行 `on_load(ctx)`。因此语法检查、脚本和工具可以正常 import `mods` 及其子模块并直接调用其中的纯函数，不会绑定端口、起线程或读写运行数据；`mods.module_names()` 是文件/包模块命名规则的唯一实现，`run.py --check` 复用它而不是抄一份。`boot()` 中途失败会先执行已加载模块的退出钩子再抛出。同阶段可通过 `LOAD_BEFORE` / `LOAD_AFTER` 声明少量顺序。完整顺序会在执行钩子前计算，未知阶段、反向跨阶段关系和循环会使启动失败。

`ctx` 保存 Import 成功的模块；`available` 只包含 Load 成功的模块。可选模块失败会记录并隔离，其命令和 capture 不会继续暴露。`bot`、`command`、`connect`、`context`、`message`、`storage` 是最小运行闭环，任一不可用都会阻止启动。退出时，成功加载模块的 `on_exit()` 按逆序执行；单个退出失败不阻止后续清理。

这个“required core 成功、可选功能隔离失败”的判定同时用于生产启动和 `run.py --smoke`。因此 smoke 退出码 0 只证明最小运行闭环完成加载并能退出，不表示所有公开 Module 都可用；`available/ctx` 计数和可选失败列表必须按[运行文档的判读规则](runtime.md#离线检查命令与结果判读)理解。

Module 顶层应以定义和注册为主。端口绑定、storage 读取、scheduler job、worker 和外部 client 初始化放进 `on_load()`；长驻资源在 `on_exit()` 收束。外部服务 client 尽量延迟到实际调用。

## 唯一消息路由

`mods.bot` 持有唯一事件循环。OneBot 事件始终以普通 `dict` 在系统中传递，不建立 DTO 或消息 class 层级。当前入口顺序由[交互模型](interaction-model.md#入口有优先级)记录：日志、延续式交互、点命令、shell、线性 link/capture 共用一条明确路径。

路由不改写事件字典。`message` 从 NapCat 送来是什么，写进 chatlog、进 `history`、送到模型面前就还是什么；回复关系、去掉回复后的正文和 at 列表是它的三个投影，由 `mods.msgs` 的纯函数 `reply_cq()` / `body()` / `at_cq()` 现算。这不只是风格：`bot` 曾在写完日志之后就地剥掉开头的 reply CQ 并挂上 `reply` / `at_cq` 两个键，于是实时事件和从同一份文件重建出来的那条事件不一样——重建的带着 reply CQ、却没有那两个键，模型因此只在重启后看见字面的 `[CQ:reply,...]`。派生值不落在数据里，这个差别就无从产生。规则是：**解释**消息的地方（`^C`、`.` 命令、shell 前缀、唤醒词、link 条件、延续式回答、`.py` 的 `input()`、重复提醒判定、单 CQ 判定）读 `msgs.body()`，**存储和显示**消息的地方（chatlog、history、模型上下文、图片预缓存）读原始 `message`。

`body()` 去掉的不只是 reply。QQ 客户端引用回复时会紧接着插一个 at，只去 reply 会留下 `[CQ:at,qq=…] .cave add`，而下游全是前缀判定，于是「引用着发命令」静默失效——这是旧 `_strip_reply` 留下的缺陷，不是新的。因此 `body()` 连开头的 at 一起去掉，并清理它们带的空白；**只去开头的**，命令后面当参数用的 at 原样保留。at 列表则由 `at_cq()` 读一个只去了 reply 的内部投影，否则「谁 at 了我」会随 at 的位置而变——`chat.has_at` 正是靠这一点在 `body()` 已经把 at 去掉之后仍然认得唤醒。

当前事件与等待下一条输入由 `mods.context` 持有；近期 256 条窗口事件由 `mods.history` 持有；完整可读聊天历史由 `mods.chatlog` 追加写入。三者职责不同，不能相互替代。

`chatlog` 的行格式是协议，逐条写在[chatlog 记录格式](chatlog-format.md)里，改动即破坏性变更。记录形状是「记录头顶格、正文缩进 4 格」：`【头衔】名字(QQ号) 时:分:秒 | 消息号`，私聊同形但没有 `【头衔】`。正文存 OneBot 原始串，末尾空白按规则清除；`chatlog.display()` 是唯一的显示投影，终端回显和 `.search` 都经过它把正文 unescape 回可读形式，因此人看到的内容与切换前一致，而文件保留的是事实。`.search` 也按这个形状分组：命中任何一行都返回它所属的整条记录，附 `path:line` 定位头（通知没有正文，是一条单行记录），所以搜正文能看到是谁说的、搜人名能看到说了什么；结果是记录原文，不经过 `parse_log`。`format_message` 与 `parse_log` 是互逆的一对纯函数，`parse_log` 同时能读切换前的旧记录并用 `_derived`/`_missing` 标出哪些字段是推导值、哪些根本没写下来。切换时刻记在 `storage` 的 `chatlog/format`。历史记录只读，不重写；旧记录缺私聊发送者身份，这一缺口的确切大小和其余取舍见[统一消息模型](working/proposals/message-model.md)。

因为写入总是先落盘再进内存，文件是内存的超集：`history.getlog(msg)` 不带范围时仍是一次 dict 查找、返回活列表，给了 `since`/`until` 则转由 `chatlog.read_range` 只读文件并返回带来源标记的新列表。代价写在签名里而不是藏在名字后面，热路径因此不为罕见的范围查询付固定成本。

`history` 因此是纯内存，不再自己持久化：启动时由 `chatlog.on_load` 从文件重建近期窗口，只重建切换点之后的 v1 消息记录与戳一戳，并剔除已被撤回的消息——v0 正文是投影、v0 私聊没有发送者，放进内存都是静默的错误内容。戳一戳和撤回是 notice 里仅有的两个例外，因为它们各自的消费者只需要行尾那个 id，而那个 id 是写入侧自己写下去的；其余 notice 仍是不透明文本。重建不出东西时内存就是空的，随新消息长回来。

## 命令：函数就是入口

命令由无参数 `@command` 注册。命令名从模块名和函数名机械派生：

- `mods.bar.run` 对应 `.bar`；
- `mods.bar.foo` 对应 `.bar.foo`。

命令函数继续接收命令名后的原始 `body`。返回普通值会发送；返回 generator 会登记为当前用户、当前窗口的 continuation。Import 或 Load 失败模块留下的注册会被过滤，因此失败功能不会占住点命令入口。

命令依然处于 Bot 进程的宿主机信任域。权限不是统一中间件；高权限函数必须显式调用 `mods.op`。人类命令按事件作者查 `ops`，模型工具按 `config.bot_permissions.op` 查 Bot 自身固定权限；模型通过 `cmds__run_command` 显式代行时还按被代行者查 `ops`，但 Bot 自身没有 op 权限就不能代行人类 op。普通模块应直接 import 已知依赖，不通过命令注册表反查模块。

## `.py`、`pyload.py` 与动态名称

`mods.py` 在 `LATE` 阶段建立一份共享 `loc`：

1. 放入所有 Import 成功的模块及 `ctx`；
2. 合并版本控制内明确维护的 `DYNAMIC_EXPORTS`；
3. 在候选字典中执行 `data/pyload.py`；
4. pyload 全部成功后才替换正式环境，失败则只安装版本控制内环境。

稳定公共 helper 已升格为普通 mod，并在确有 live link 消费者时显式平铺导出。`data/pyload.py` 只保留设备值和现场试验代码，不再与正式 helper 形成第二份实现。模块名不可被 pyload 覆盖；覆盖显式平铺名会在启动终端列出。

动态环境的核心不依赖可选设备：设备 Module 加载失败时，其平铺别名不会进入 `DYNAMIC_EXPORTS`，但 `mods.py` 仍会安装其余环境。当前 Minecraft 的六个控制别名采用这一规则；没有对应设备配置的实例会得到一个不含这些名称的正常 `.py` 环境，而不是连带失去 link、later 和 todo。

动态代码只有在模块名本身运行期计算时才使用 `getmod(name)`。已知模块依赖直接 import；旧 `getcmd` 不再存在。旧根 `data.json` 已退役，动态环境中的 `data` 是 `storage.get("", "py_data")` 的直接引用。

## 统一 LLM 工具模块

`mods/tools/` 是工具和可按需载入说明的唯一目录，`mods.tools.ToolRegistry` 持有进程级 last-good 模块表。顶层 `foo.py` 和 `foo.md` 具有同一种模块语义：第一行是目录描述，余下文本在激活后进入中心 agent 的正式工具状态 input；子代理仍在开头显示一次。Markdown 模块没有函数，Python 模块通过 `__all__` 导出一组普通函数。Python 文件可以正常 import 第三方依赖、其它 `mods` 和同目录 `_helper.py`，导出函数仍由现有 `Tool` 校验，模型侧名称通常为 `foo__function`。

首次使用 registry 时，每个顶层模块独立尝试进入 last-good；单模块失败只记录 traceback。之后修改磁盘不会自动改变运行版本：`list_tools` 查看差异，`reload_tools` 才逐模块读取、执行、校验并原子替换 last-good，失败保留旧版；`load_tools` 不读磁盘，只激活 last-good 模块。中心 agent 的实际激活名单及最后使用时刻在全局 `agent.active_tools`，超过 1 小时未使用的模块下轮不再装回；函数定义模块顺序另存在 `agent.tool_schema_modules`，卸载不删除定义，调用只返回重装提示。请求边界先同步共享 registry 与本会话函数，再把完整或差异状态写入正式 input，之后更新 `agent.tool_state_told`；磁盘漂移仍只作末尾 hint。没有工具状态 system 基线，也没有旧内存通告。

`meta.py` 是唯一默认激活的必需模块，保存工具维护说明并导出 `exec_code`、`list_tools`、`reload_tools`、`load_tools` 四个无前缀恢复入口。`__init__.py` 只持有 registry、last-good 和 per-Chat binding 机制；它不再伪装成工具格式。`meta` 调用通过一次调用范围内的 `ContextVar` 取得当前 binding，多 Chat 和 `assign_tasks` 工作线程不会共享错误会话；磁盘删除 `meta.py` 的 reload 会失败并保留旧 last-good。

中心 agent 的可用模块描述从首次工具状态 input 开始可见；持久激活模块从全局 `agent.active_tools` 装回。`load_tools` 更新当前 Chat 的实际模块和函数，`reload_tools` 更新 last-good；每个 LLM 子请求冻结同一份 schema/callable 快照，所以加载或重载只从下一子请求起生效。

提示词没有新增编辑管理器；`.py` 共享环境继续暴露可变 `prompts` 对象，模型需要时可自己编写工具编辑它。

## 线性 link 与 capture

`data/storage/links.json` 的权威格式是有序 JSON 数组，每项只有：

```json
{"name": "名字", "type": "re 或 py", "cond": "条件", "action": "动作"}
```

分派按数组顺序检查，首个命中节点执行 action 后结束；不存在 `succ`、`fail`、`while`、反向边或递归图遍历。新节点插入列表头，可用 `.link move` 显式调整优先级。action 失败会记录并结束本次已命中的反应，不继续声称其它节点命中。

版本控制内的通用捕获由独立 `mods.capture` 提供。`@capture` 默认排在持久节点之后，`@capture(before="link-name")` 可插在指定持久节点之前。它只在所属模块 Load 成功后激活，不写入 `links.json`；捕获异常会记录并继续后续线性节点。

`re` link 使用结构化模板捕获并将最后一行表达式结果自动发送；`py` link 直接使用共享 `loc`，action 需要自行发送。条件和动作仍是受信任的动态 Python 配置，不是沙箱。

## Storage 与设备状态

`mods.storage` 是普通业务状态的唯一持久化层：命名空间对应目录，键对应 JSON 文件，调用者取得普通可变对象引用。运行期间内存是写入权威，文件是可人工检查和修复的投影。

后台同步以 baseline 避免无变化重写；外部合法 JSON 变化只有在内存仍等于共同 baseline 时才进入内存。显式 `storage.load()` 可强制选择磁盘版本，`storage.save()` 强制保存当前对象。写入使用同目录临时文件、`fsync()` 和 `os.replace()`。

删除普通 JSON 文件只会等待内存重建文件；裸文本 `DELETE` 或 `storage.delete()` 才表示业务删除。非法 JSON 不会用空默认值覆盖有效内存。

`config.json` 仍是管理员和 Bot 昵称的专用权威；`.env` 与 `data/device/` 保存设备地址、密钥路径等私有部署值；`data/pyload.py` 保存动态环境的设备输入。它们不能被混成通用 storage，也不能复制到文档或版本库。

## LLM、图片与可观察输出

`mods.llm/` 持有模型配置、client、流式响应与每请求工具快照；`mods.chat/` 按 `__init__.py`（公开入口、全局设置与 `data/hint/` 下的窗口 `#hint` 文件）、`agent.py`（中心激活、同一段上下文的追加、交接与夜间交接反思）、`windows.py`（窗口种类：标签、解析和各种类的发送函数；QQ 的两种与她自己的 `me` 也在表里）、`segment.py`（`data/context/` 下的当前一段与归档）、`roles.py`（召回工具、交接指令与日记汇总）、`awaker.py`（按笔记 awaker 的规则每分钟检查一次的闹钟）、`subconscious.py`（潜意识：她忙完一阵后用自己的一条上下文整理她那条，默认关）、`screen.py`（按 chatlog 原文格式渲染窗口画面）、`actions.py`（把 oplog 里的输出与结果渲染成可读行动记录）、`notes.py`（`data/notes/` 下的笔记、历史版本与策略文件）、`view.py`（系统提示与事件投影）、`reader.py`（未读标记）、`subcommands.py`（`#` 设置命令）划分职责，对外仍是一个公开 Module；`mods.oplog/` 的 `__init__.py` 持有唯一追加日志，启动时只重建还在读的状态（未读到达、激活、未确认通知、她自己的输出与结果），`replay.py` 放写前校验和逐行重放规则；`mods.tools` 持有统一 registry、当前 Chat 绑定以及按职责分类的真实工具实现，天气模块只投影现有 `mods.weather`；`mods.image/` 持有图片身份、缓存和视觉输入；`mods.forward` 持有合并转发的取回、落盘与渲染——转发的 id 和节点里的图片 url 都会过期，所以取回来的节点落在 `data/forward/<id>.json`，`.cave` 存转发时走的就是它，发送方向则由 `send_forward_msg` 的节点数组承担。`mods.websearch` 持有互联网检索，把 DeepSeek 的服务端 `web_search` 包成一个普通函数——密钥与聊天共用，搜回来的外部正文只当数据交回，不当指令；每次请求的 token 按 Flash 单价、服务端搜索按次（北京时间 2026-11-01 起每千次 5 元）记进 Bot 账。`mods.bili` 持有柚子自己的 B 站号：登记窗口种类 `bili`（一个评论区一个窗口，发送函数发评论）、扫码登录、每两分钟把回复和 @ 推进窗口；所有 bilibili-api 请求排进模块自己的一个常驻事件循环，因为库按事件循环缓存 HTTP 客户端。刷视频、读字幕和评论的工具在 `mods/tools/bili.py`。`mods.browser` 持有自管的常驻 Chromium 与裸 CDP 会话：打开页面、读渲染后的正文、在页面里跑 JS、截图交给视觉链路，每个聊天窗口一个标签页；进程由 `watchdog.detached()` 起，不随工具调用被 `^C` 带走；顶层导航只校验 http/https 地址语法，不解析或限制本机、内网、公网等目标，也不拦截页面的后续请求；启动就绪后清掉 Chrome 按 profile 恢复出来的遗留标签页，让每次启动都从一页空白开始。`mods.phone` 持有她自己那台安卓备用机（维护者定的：她想用就用，不设 op 门槛）：第一次要看屏幕时经 adb 推送并起 scrcpy-server 1.25，由后台线程常驻一条 H.264 流，只攒最近一个关键帧以来的包，看的时候交给 ffmpeg 解出最后一帧，整张黑才当屏幕灭着去点亮；半小时没人用就关流。点、滑、按键、打字只管做、不带图（维护者：截图和操作分开，顺手带回的图常常太早或太晚，她也就不主动看了），要看由她自己挑时候 `look`。`frames(continuous=True)` 在后台一直截、攒满一组就拼好，每次调用交回一组；后台截的存成 `frame-*.jpg`、拼完就删，不算她看过的那张，也不挤掉她上下文里引用着的截图（截图留最近 300 张）。看视频这类会动的东西用 `frames`：那段时间里读流线程把收到的包另抄一份，最后只调一次 ffmpeg 解出按时间取的那几帧，拼成一张网格图给她。点、滑、按住走同一条 scrcpy 连接的控制通道：坐标就是她最近看的那张图上的，server 自己换算；按下和抬起是两条消息，所以能推着摇杆不放、边走边看，另一根手指照样点，一分钟没碰手机就自己松开；转屏或流重连过后要她先看一眼再点。她可以像模拟器的键位映射那样定键位（按钮、带半径的摇杆，存在 `data/phone/marks.json`），`look` 可以把键位和坐标线画在图上，也可以用 uiautomator 读一遍界面、把控件框出来编号（游戏画面读不出）；画了标注的那张另存，和干净的原图一起给她，因为标注会挡东西。`aim` 转视角对准一个东西：拖一下、在新画面里用模板匹配找到它、按它实际挪了多少修正，量出的拖动比例存在视角键位里当下次的起点（3D 场景里近处远处挪得不一样，比例算不准，靠的是闭环）。这些后端函数她也能在 `exec_code` 里组合着用（`find`、`stitch` 只给脚本用）；`exec_code` 在调用方上下文的副本里跑，脚本里也能用 `_vision.attach` 把图交给她。按键走 `adb shell input`；中文经 ADB Keyboard 输入法的广播打字；输入法被切成搜狗时，`keyboard` 打开输入法设置页、用 uiautomator 读出开关去点（手机上的 shell 没有权限直接设）。设备地址和 server 路径在 `data/device/phone.json`，截图在 `data/phone/shots/`（留最近 30 张）；工具在 `mods/tools/phone.py`。图片与子任务工具只惰性调用 `mods.chat` 的既有 usage/cost 入口，不复制计费状态。`llm`、`tools`、`chat`、`oplog` 和 `image` 采用文件夹 Module，是因为各自内部实现共同拥有明确状态；具体能力仍优先由普通函数表达。

来源窗口的到达与激活由 `oplog` 的 append-only 日志登记，`context` 只保留每窗口锁与一次路由期间的事件对象→arrival 关联：路由在锁内先写 chatlog/history 再登记 arrival。唯一中心 agent 用 `AGENT_WINDOW` 持有全局执行线；激活只产生可确认的通知，模型第一次输出时确认。

中心 agent 只要不交接，就一直在同一段上下文里（2026-10-22 起，取舍见[无状态主循环提案](working/proposals/stateless-agent-loop.md) §7.2）。`chat.segment` 把这一段存在 `data/context/current.json`：系统提示每次重建，其余消息（包括原生 assistant/tool 配对、思考和工具状态）原样保存，每轮结束后更新，重启后接着用。新一段由 `agent._head` 开头：global、recent、最近一篇日记和只列名字的目录；之后每次唤醒由 `agent._run_agent` 往后追加第一次出现的窗口与作者的笔记、叫醒她的窗口画面和叫醒原因，前面的消息不动。系统提示最前面是笔记 self（`chat.get_prompt`，旧提示词第一次读到时由 `_migrate_self` 搬入），注意事项末尾是从 `tools/memory_system.py` 读出的"你的记忆怎么运作"。私聊唤醒期间 `agent._keep_typing` 每 5 秒调一次 NapCat 的 `set_input_status`，群聊没有这个状态。

窗口画面直接读 `chatlog/` 的普通日志文件、保留原文格式，不分窗口种类（QQ 以外的窗口由 `chat.push` 写入同样形状的记录），`storage` 的 `agent.seen` 记各窗口看到的时间，用来标出新消息。这一段里第一次出现的窗口给 `max_events` 条，已经出现过的从这一段里上次给到的那条记录之后接着给。第一次输出时记下看到的时间，并对画面里的窗口调用 `reader.mark_window_read` 把未读登记标为已读；只标到读画面时取下的到达边界。之后每次子请求之前，`agent._late_arrivals` 把这些窗口新到的未读追加进这一段（Cortico 式的轮次边界投递；画面和插话是带框头的 user 消息，系统自己说的话是对话中间的 system 消息，见 `view.outside`），那次请求产生输出后再标已读，所以生成期间同窗口到的消息不再等收尾接续；别的窗口新到的激活仍由收尾接续。

交接（`agent._handoff`）在三种情况下发生：她调 `tidy`；`data/notes/policy/tidy.py` 里的 Python 条件成立（默认是这一段超过 `#limit` 的整理阈值 `agent.tidy_token`）；夜里 `reflect` 规则到点。交接就是在同一段末尾追加 `roles.handoff_prompt`，让她用同样的上下文和工具确认各类信息已经在 recent、人和群的笔记、global、todo 和当天日记里，`type_word`／`send` 在交接时拒绝发言。正常跑完（没出错、没被打断或停下、最后一次回复不再调用工具）后这一段连同交接往来归档到 `data/context/archive/`，`current.json` 删掉；没完成就原样留着下次再交接，`yz.agent` 记一条 warning；连着失败 2 次或长到整理阈值的 2 倍时不再等她确认，原样强制归档并在下一段开头告诉她。笔记是 `data/notes/` 下她随时能用 `write_note` 改的普通文件，内容没变不写，改写和删除前把旧文件复制到 `.history/`，所以交接可以重复。`chat.awaker` 每分钟按笔记 awaker 的规则检查一次（`on`／`wait` 规则另由 `capture_chat` 在消息到达时认领，一次性规则响过就划掉；`follow`／`mute` 覆盖层由 `awaker.triggered` 在默认触发器之后、`capture_chat` 和 `chat.push` 叫醒之前查，到期划掉；她 `send` 以后 `awaker.spoke` 让这个群自动 `follow` 几分钟），到点时用 `chat.push` 把规则理由写进她自己的 `me` 窗口并叫醒她（她正忙时这一轮收尾接上），或者用 `agent.reflect` 先交接、汇总月记年记，再用新的一段加载 `memory_system` 反思：从症状出发读归档的上下文、聊天记录、行动记录和笔记历史版本。反思这一段也归档。她调 `dismiss` 时 `agent.request_dismiss` 查过范围，这一轮结束后 `_drop_since` 按这一段里的记号（`marks`，每次唤醒追加之前记一个）把这一段退回去。`#subconscious on` 时，`_drive_agent` 在她忙完一阵、没有别的召唤在等时，于同一个 turn 里调 `subconscious.settle`：潜意识用自己的一条上下文（`data/context/subconscious.json`）看她那条里它还没看过的部分，按最后一行决定由 `agent._fold_since` 把末尾、或由 `agent._fold_between` 把中间几次唤醒换成一句记号（原样另存为 `-folded` 归档段；收中间时后面记号的下标跟着挪，区间里第一次给的笔记退回去下次再给），由 `_drop_since` 放下（只到最后），或者不动；列给它的每次唤醒后面写着那之后约多少 token，收中间时那些要按原价重读；它那条长了自己写地图换新的，旧的归档为 `-subconscious`。以前的单独图书管理员会话和 `data/journal/` 记忆系统日志已删除。

输出在派发工具前持久登记，同一模型输出的同步工具返回整批登记一个 result；这些仍是 `oplog` 事实，供交接之后用 `read_actions` 读回。旧阅读模型留下的行（正式阅读 input、覆盖、`condensed`/`clear`/`floor`、离线补回信源）仍在日志里，重放照常接受：它们对未读的影响照算，其余直接跳过、不进内存（2026-10-23 清理，见[事件流提案](working/proposals/event-stream-core.md) §7）。不认识的行让重放整体失败。`_stream_results` 到达只在未读统计里被跳过。

`oplog` 对可预见的非法记录在写盘前用与重放相同的校验拒绝，不改变日志或内存；追加、fsync 或落盘后索引应用失败会进入持久化失败态，后续写入和同批尚未派发的工具行动均停止，直到重启检查日志。离线回放入口 `run_offline` 与 `experiments/memory_replay.py` 已随旧阅读模型删除；按新循环重写回放之前，没有离线评估路径。

模型可用 `read_log`（任意窗口的 chatlog 原文，格式与画面相同，可按时间往前翻）、`search`（按线索一次找遍聊天记录、笔记、日记和归档的上下文）、`read_actions`（按行动号、最近若干条或关键词读自己的行动记录，在完整原文里筛，长的按 `start` 分页）与 `read_note` 召回画面之外的内容，用 `write_note` 改笔记、`tidy` 交接；`edit_hint` 已被笔记 todo 取代，`cover_events`、`recall_events`、`event_links` 与 `take`／`status`／`mentions`／`fetch`／`read_messages`／`mark_read`／`pull` 已从工具集移除。`聊天开始`／`聊天结束`是普通内容。旧窗口聊天设置不再读取，也不并入全局设置；窗口 `#hint` 独立保留。开机时 `_backfill` 对有可靠锚点的窗口向 NapCat 往回翻页，把离线期间的消息按时间插进普通日档（`chatlog.insert_recovered`），画面、`read_log` 和 `search` 都能看到；其中实时到达会叫醒她的（at、名字开头、私聊对方发来的）登记为激活的到达，唤醒原因写「离线时叫你」，一个窗口补完叫一次；别的补回消息不算未读、不唤醒。以前写的按日 sidecar 仍可被范围查询读到，不再新写。`#` 控制消息不投影。主模型与日记汇总的费用统一记入 Bot 账。

终端打印是实际运维界面的一部分：收发消息、流式 LLM 内容、工具调用、图片捕获/缓存、link/capture 命中、模块失败和生命周期进度都应保留可观察输出。这些产出者各自选一条**流名**（`mods.log.stream(...)`，即 `yz.*` logger），终端订阅其中一组；流是「属于哪个子系统」这条轴，与 logger 级别表达的「是不是故障」互不替代。每条流有自己的 `log/<流>.log`；`app.log` 只记严重程度；chatlog 保存产品聊天历史，三者不是同一用途。落盘的记录由一个 filter 自动补上交互归属，调用点不传参。终端行为、订阅集与 `.log` 见[运行时](runtime.md)。

## 单实例、权限与退出

一个进程服务一个 Bot 账号。cache、storage、link 环境、命令表、scheduler 和 connector 都是进程级单例；多个独立 Bot 项目共存不改变本项目内部的单实例事实。

人类侧仍以 master/op 与普通用户为权限边界；op 能直接使用 `.py`、shell、文件、link 和宿主控制能力，技术上拥有 Bot 进程和宿主机控制权。Bot 自己是否进入同一信任域由 `config.bot_permissions.op` 单独声明，不依赖 Bot 账号是否也写进人类 `ops` 名单。整数权限体系仍只是[未实现部分](working/proposals/permissions.md)。

`.reboot` 以退出码 233 请求外层监督者重启；`.shutdown` 以 0 正常退出。第一次 Ctrl+C 由 `run.py` 转发，`main.py` 进入同一 `finally`，随后逆序停止 scheduler、listener、worker、子进程与 storage。重复 SIGINT 在保存期间被抑制；SIGKILL、断电和解释器 fatal error 不经过这条有序路径。

## 历史与未完成项

旧 `_code/main.py`、`funcs.py`、`bot/`、`s3/` 和旧命令扫描器已经退出正式代码树；其能力去向见[模块手册](working/proposals/module-manual.md)和[源码覆盖记录](working/proposals/source-coverage.md)。这些文档现在是迁移记录，不是第二套运行契约。

Storage 值校验与历史版本、统一 incident/节流、更细权限和多项目协调仍从[提案索引](working/proposals/README.md)进入。
