# Mods 模块手册

> 状态：目标职责已实现并于 2026-07-29 切换为现行结构；旧来源栏仅作迁移追溯。

这份手册先按系统能力说明“谁负责什么”，再附切换前代码来源用于覆盖核对。正式代码树没有留下旧 `main.py`、`funcs.py`、`cmds` 与新 `mods` 并存的兼容路径。

表中的保留/删除判断已经按[新架构](code-organization.md)固定。所有保留文件在 `mods/` 中仍是平等的普通 Python 模块；“叶子功能”只表示它不定义 loader 或主路由，不表示次要或受限。

## 设计起点

柚子是一个可通过 QQ 操作的长期个人运行环境，不是一组聊天业务插件。目标结构先满足下面这条最小闭环：

```text
启动并装入普通 Python 模块
→ 接收一个事件并建立本次执行位置
→ 稳定命令或动态程序消费它
→ 调用同一环境中的模块并产生输出/状态变化
→ 状态和任务跨消息持续
→ 修改源码后完整退出、保存并重启
```

从这条闭环直接得到的模块只有：装入模块的 `mods/__init__.py`，运行事件循环的 `bot`，连接外部设备的 `connect`，保存长期状态的 `storage`，保存本次执行位置的 `context`，提供稳定入口的 `command`，提供全环境编程的 `py`/`link`，提供时间延续的 `scheduler`/`later`，以及 `file`/`edit`/`reboot`/`op` 这些内部维护入口。

其它文件都是普通能力：有实际功能就保留，失败或删除不应改变上述闭环。`history`、`chat`、图片处理、娱乐命令等可以重要，但不能反过来定义 loader 或 Bot 的基本形状。

当前源码只在目标边界之后出现，用来证明某项能力没有遗漏或解释旧实现为何特殊；“旧文件存在”本身不是建立目标模块的理由。

## 从旧身体提取的事实

| 当前形状 | 它实际保护的事实 | 新系统保留什么 |
|---|---|---|
| `main.py` 的固定路由顺序 | 动态 link 不能盖住命令、取消和恢复入口 | `bot.py` 中保留可读的显式分支顺序，不保留反向 import |
| 命令文件只需 `run(body)` | 新能力应当低样板、可直接返回或 yield | `@command` 只负责注册，仍传原始 body 并接受普通返回值/generator |
| `cache.thismsg()` 按线程保存当前消息 | 后台/线程调用仍要知道结果回到哪里 | `context.py` 保存当前执行位置，不把所有函数改成统一 Context 参数 |
| `.py loc` 与 link 共用 globals | 它们是在编程同一个持续环境，而非两套沙箱 | `py.on_load(ctx)` 建立一份共享执行环境，link 直接复用 |
| `from main import *` 与 import 顺序 | 旧代码借部分初始化的 `main` 获得全局名称 | 删除该实现；稳定依赖用 import，全环境引用只由 `ctx` 提供 |
| storage 返回可原地修改对象 | 持久状态应像普通 Python 数据一样直接使用 | 保留引用语义与明确的磁盘覆盖入口，不引入 repository/model 层 |
| `.edit/.file/.py` 后完整重启 | 源码可修改，但运行状态应一次原子切换 | 保留 supervisor + 完整进程重启，不做通用热重载 |
| import 时启动 socket/thread/job | 旧 import 同时承担定义和启动 | 只保留其运行需求，启动移入 `on_load`，关闭移入 `on_exit` |
| py/link 中大量平铺名称 | 设备脚本和实时反应把源码公共函数当作交互词汇 | 迁移前审核真实需要的词汇；不靠星号导入偶然生成，也不默认全部删除 |

没有出现在这张表中的旧技巧，只有在能指出保护的行为、外部约束或恢复用途后才进入目标实现。

## 入口和生命周期

| 文件 | 唯一职责 | 当前来源 |
|---|---|---|
| `run.py` | 解析启动参数、监督子进程、按退出码重启 | 现有 [`run.py`](../../../run.py) |
| `main.py` | 安装退出信号保护，`import mods`，调用 `mods.bot.run()`，最后调用 `mods.exit()` | 现有 `_code/main.py` 的信号与最外层 `try/finally` |
| `mods/__init__.py` | 扫描 import、建立 `ctx`、计算加载顺序、调用 `on_load`、记录成功顺序、提供 `exit()` | 新文件；规则见 [Mods 两阶段加载](mods-loading.md) |

`on_exit()` 只对已标记为可用的模块调用，顺序与可用顺序相反。一个 `on_exit()` 报错只记录并继续。没有 `on_load()` 的模块在排到自身时直接成为可用，因此运行期间可能创建子进程等资源的模块可以只定义 `on_exit()`；这套钩子只服务完整进程启动和退出，不提供热重载。

模块 import 成功只表示定义可见。没有 `on_load()` 的模块在 Load 排到自身时可用；声明了 `on_load()` 的模块只有在钩子成功后才可用。命令注册表可以在 Import 阶段收集装饰器结果，但帮助和分发只暴露可用模块的命令，因此 import 或 `on_load` 失败的功能不会占住命令名。

## 支撑最小闭环的模块

| 目标模块 | 负责 | 不再负责 | 当前来源 | 生命周期与顺序 |
|---|---|---|---|---|
| `bot.py` | 唯一消息循环；保持命令、shell、link、notice 和阻塞交互的现有优先级；处理 generator 返回值 | 不导出其它模块名称，不加载具体功能 | `_code/main.py` 的 `recv`、`_strip_reply`、`_set_catches`、`_iter_ret`、`_cmd_ret`、`_run_bash` 和循环 | `run()` 在全部 Load 完成后由 `main.py` 调用 |
| `command.py` | 无参数 `@command`、模块/函数派生命令名、点命令匹配、`params`、`grouponly`、帮助查询 | 不接受手写命令名，不扫描第二个命令目录 | `_code/bot/cmds/__init__.py` | Import 阶段声明；`bar.run → .bar`，`bar.foo → .bar.foo`；分发时过滤不可用模块 |
| `capture.py` | 进程内 `@capture` 声明、可选持久节点前定位和 Load 后激活 | 不读取或写入 links storage，不执行条件/action 字符串 | 新架构能力 | Import 只收集；所属模块 Load 成功后激活；默认作为末尾兜底，已证明的旧优先级用 `before` 合并进同一条线性顺序 |
| `connect.py` | OneBot HTTP 入站 socket、`recv_msg()`、`call_api()`、出站端口映射 | 不发送业务消息，不启动消息循环 | `_code/bot/connect_with_http.py` | `PHASE = INFRA`；`on_load` 才 bind/listen；`on_exit` 关闭 socket |
| `config.py` | `config.json` 的最小读写和首次配置格式 | 不承载普通功能数据 | `_code/s3/config.py` | 纯函数；实际读取由身份初始化触发 |
| `storage/` | `data/storage` 的加载、原子保存、内外变化同步与显式磁盘覆盖；`codec.py` 保存纯投影格式 | 不保存近期消息缓存或 `.py` 私有状态 | `_code/s3/storage.py` | `PHASE = INFRA`；`on_load` 加载并启动 watcher/worker；`on_exit` 停止并保存 |
| `scheduler.py` | 单个 APScheduler 实例及其关闭等待 | 不注册具体功能的 job | `_code/s3/scheduler.py` | `PHASE = INFRA`，在 `message` 后加载，使退出时先等任务、再收束发送队列并保存 storage |
| `thread.py` | `to_thread`、`SimpleFuture` 和受 Ctrl+C 控制的线程包装 | 不启动后台业务任务 | `_code/s3/thread.py` | 纯定义 |
| `log.py` | 进程日志格式和唯一 handler 配置 | 不写 QQ 聊天记录 | `_code/s3/__logging.py`；旧 `_code/s3/log.py` 不保留第二套实现 | `PHASE = INFRA`；`on_load` 安装 handler，`on_exit` flush/close |
| `context.py` | 当前线程事件、最近一条事件、交互线 key 与等待下一次输入的 continuation | 不保存窗口历史，不查询用户资料 | `_code/bot/cache.py` 的 `msgs_this`、`thismsg`、`catches`；`_code/main.py` 的 `msg_id` | 纯内存状态；generator 与 `.py input()` 共用可取消的等待登记 |
| `history.py` | 每群/私聊最近 256 条消息、窗口键 `window`、作者 `author`、`getlog`（带 `since`/`until` 时转 `chatlog.read_range` 读文件）/`same_times`/`any_same`/`get_one` | 不写完整聊天日志，不解析日志文件，不自己持久化，不保存 LLM 会话对象 | `_code/bot/cache.py` 的 `msgs` 与查询；`_code/funcs.py` 的 `match`、`getlog`、`msglog` | `PHASE = INFRA`；纯内存，启动时由 `chatlog.on_load` 从日志重建，退出时无需保存 |
| `identity.py` | Bot 自身资料、用户/群资料缓存、名字与群名、群成员、用户/群 storage 便捷函数 | 不决定管理员权限 | `_code/bot/cache.py` 的资料缓存；`_code/funcs.py` 的 `ensure_*`、`get/setname`、`get/setgroupname`、`memberlist`、`headshot` | `PHASE = INFRA`，在 `connect` 后完成登录与首次昵称配置 |
| `message.py` | `send()`、当前窗口 `sendmsg()`、内部事件 `recvmsg()`、发送节流，以及发送后的日志回写 | 不决定路由优先级 | `_code/main.py` 的 `send`；`_code/funcs.py` 的 `sendmsg`、`recvmsg`；`_code/s3/delay_func.py` | `PHASE = INFRA`；发送线程仍按需启动，生命周期顺序只保证 scheduler 先停、队列再排空、storage 最后保存 |
| `chatlog.py` | 按群/私聊写完整聊天日志、格式化 notice、把事件加入近期历史 | 不提供近期查询和 LLM 上下文裁剪 | `_code/bot/chatlog.py` | 无后台任务 |
| `msgs.py` | OneBot 事件类型判断 | 不保存事件状态 | `_code/bot/msgs.py` | 纯函数 |
| `cq.py` | CQ 解析/转义、图片 CQ 生成和 QQ 媒体下载 | 不承担 LLM 图片语义缓存 | `_code/bot/cq.py` | 顶层只编译正则和建锁 |
| `pages.py` | 纯分页与 yield 翻页交互 | 不保存 session | `_code/bot/pages.py` | 纯函数 |
| `text.py` | `read_params`、link 模板替换和少量字符串函数 | 不解析命令注册或业务数据 | `_code/s3/str_tool.py`；`_code/funcs.py` 的 `getint`、`ls` | 纯函数 |
| `file.py` | 文件读写 helper 和 `.file` 命令的完整宿主机文件能力 | 不负责 `.edit` 缓冲或 `.dir` 命令语法 | `_code/s3/file.py` 与 `_code/bot/cmds/file.py` | Import 阶段注册 `.file` |
| `repl.py` | 可复用的长驻子进程 REPL | 不决定具体语言命令 | `_code/s3/repl.py` | 子进程在命令实际调用时启动；`on_exit` 关闭仍存活的子进程 |

## LLM 与图片

| 目标模块 | 负责 | 当前来源 | 生命周期与顺序 |
|---|---|---|---|
| `image/` | 图片 URI/内容身份、下载与内容缓存、描述缓存、长图切片和视觉输入转换；身份与视觉切片分别位于同一 package 内 | `_code/s3/image_identity.py`、`url_to_base64.py`、`image_description_cache.py`、`vision_input.py` | `PHASE = FEATURE`；`on_load` 建缓存目录并从 storage 接入 alias；内部文件不单独 Load |
| `llm/` | 模型配置解析、供应商 client、消息/工具调用循环、响应类型；模型目录、工具描述和值类型位于同一 package 内 | `_code/s3/chat.py` 与仍被其使用的 `_code/tool.py` | `PHASE = FEATURE`；`on_load` 从 storage 建立共享 client；不在 import 时访问外部服务，内部文件不单独 Load |
| `chat.py` | `.chat`、QQ 窗口上下文、旧 `#` 管理子命令、提示词/模型/图片档位、当前工具集合和 link 的聊天入口 | `_code/bot/cmds/chat.py`；`_code/funcs.py` 的 `getchatstorage`、`msg2chat`、`has_at` | `PHASE = FEATURE`，在 `storage`、`llm` 和 `image` 后初始化 |

旧 `_code/funcs.py` 第 241–416 行的两套图片下载/`msg_split` 实现由 `image` 与当前 chat 语义覆盖，不迁移；保留的是第 418–456 行仍被聊天路径使用的消息转换语义。

## 动态执行与持续任务

| 目标模块 | 负责 | 当前来源 | 生命周期与顺序 |
|---|---|---|---|
| `py.py` | `.py`、共享 `loc`、可被 `^C` 唤醒取消的聊天版 `print/input`、执行 `pyload.py` 试验代码 | `_code/bot/cmds/py.py` 的执行部分 | `PHASE = LATE`；`on_load(ctx)` 先建立基础环境，再在候选环境执行 pyload，全部成功才采用试验名称；试验失败不使 `py` 本身 Load 失败 |
| `link/` | 四字段持久节点、进程内 capture、首个匹配 action、线性编辑和 `.link` 命令；schema 位于同一 package 内 | `_code/bot/cmds/py.py` 第 152–426 行与 `_code/bot/cmds/link.py` | `PHASE = LATE`，在 `capture/py` 后；两种节点合并成一条顺序，不保留图边或双向引用 |
| `bottles.py` | link 驱动的瓶子游戏和当前聊天窗口状态 | 旧设备 `pyload.py` | 稳定 helper 升格为 mod；不再依赖旧 `getchatstorage/_print` globals |
| `codec.py`、`timeutils.py` | live link 使用的编码、校验与时间计算 | 旧设备 `pyload.py` | 纯函数 mod；由 `DYNAMIC_EXPORTS` 提供原平铺名称 |
| `later.py` | 一次性延时任务、持久列表、恢复 scheduler job、`.later` | `_code/bot/cmds/later.py` | `PHASE = LATE`，`LOAD_AFTER = ("py", "scheduler")`，因为恢复任务会调用 `.py` 环境；不照搬私有 `Timer` 调度器 |
| `todo.py` | 周期条件列表、scheduler job、`.todo` | `_code/bot/cmds/todo.py` | 保留；删除重复/损坏解析器，由 `on_load` 向 scheduler 注册，不启动自己的永久循环 |

`py.on_load()` 先把 `ctx` 中的模块按模块名放进执行环境，例如 `storage`、`message`、`identity`，再合并版本控制内的显式 `DYNAMIC_EXPORTS`。当前 live link/pyload 使用的 `sendmsg`、`getname` 等平铺函数已经确认需要保留；具体映射与 `py.py` 放在一起，不能靠新的星号导入偶然生成。模块名不可被 pyload 覆盖；平铺 alias 的设备覆盖会在启动汇总中明确列出，并以最终 `loc` 为运行时解释结果。动态查询模块使用 `getmod`；已知依赖仍直接 import。

旧 `.py` 根目录 `data.json` 持久入口删除。新动态环境仍提供 `data`，但它是 `storage.get("", "py_data")` 的直接引用；旧文件内容不导入，不保留双写或 fallback。

## 命令与具体功能

每个保留的命令文件仍是普通 mod，并用无参数 `@command` 注册函数。`run(body)` 使用模块名作为命令；其它函数使用 `模块名.函数名`。body、返回值和 yield 语义不变。

| 目标模块 | 范围 | 当前来源 | 迁移判断 |
|---|---|---|---|
| `answer.py` | 答案之书静态内容与 `.answer` | `answer.py` | 保留；live link 仍调用 |
| `bbx.py` | 百变小猫/小动物共用设置与语料筛选 | `_bbx.py` | 随 `bbxm`/`bbxdw` 保留 |
| `bbxdw.py` | 百变小动物配置命令 | `bbxdw.py` | 保留为叶子功能 |
| `bbxm.py` | 从聊天日志重建百变小猫语料 | `bbxm.py` | 保留；live link/pyload 仍调用 |
| `cave.py` | 回声洞状态、随机池、导入导出和 `.cave` | `cave.py` | 保留；storage 读取移入 `on_load` |
| `change.py` | 易经起卦与 `.change` | `change.py` | 保留为叶子功能 |
| `chat4.py` | `.chat4` 兼容入口 | `chat4.py` | 保留明确别名，不建立第二套 chat |
| `chattop.py` | LLM 调用统计查看 | `chattop.py` | 随 `chat` 保留 |
| `cpp.py` | C++ 临时编译和交互子进程 | `cpp.py` 与 `_code/funcs.py` 的 `run_process`/`read_process` | 保留为按需启动的设备叶子功能 |
| `dir.py` | `.dir` 的 zip 目录收发 | `dir.py` | 保留命令名，调用 `file`；不复制文件实现 |
| `echo.py` | `.echo` | `echo.py` | 保留 |
| `edit.py` | 文件编辑 session、分页、撤销重做和 `.edit` | `edit.py` | 保留；storage 读取移入 `on_load` |
| `help.py` | 可用命令列表和 docstring 查询 | `help.py` | 保留；只读 `command` 注册表 |
| `hs.py` | GHCi 会话 | `hs.py` | 保留为按需启动的设备叶子功能 |
| `jm.py` | 漫画搜索、下载和发送 | `jm.py` | 保留；依赖和 client 继续延迟到实际调用 |
| `join.py` | 群内参与名单与随机抽取 | `join.py` | 保留为叶子功能 |
| `jrgz.py` | 今日鸽子 | `jrgz.py` | 保留为叶子功能 |
| `jrlp.py` | 今日老婆与禁用群配置 | `jrlp.py` | 保留为叶子功能 |
| `jrrp.py` | 今日人品 | `jrrp.py` | 保留为叶子功能 |
| `jrxdw.py` | 今日小动物 | `jrxdw.py` | 保留为叶子功能 |
| `jrxm.py` | 今日小猫 | `jrxm.py` | 保留为叶子功能 |
| `js.py` | Node REPL | `js.py` | 保留为按需启动的设备叶子功能 |
| `kmmm.py` | 历史图片 API | `kmmm.py` | 删除：已知可能不可用且没有其它消费者 |
| `lua.py` | Lua 临时文件执行 | `lua.py` | 保留为按需执行的设备叶子功能 |
| `mcf.py` | Minecraft datapack/mcfunction 编辑 | `mcf.py` | 保留；设备配置不进入版本库，补齐 op 检查 |
| `mcversion.py` | 版本查询、订阅和定时检查 | `mcversion.py` | 保留；job 在 `on_load` 向 scheduler 注册 |
| `nim.py` | Nim 临时编译执行 | `nim.py` | 保留为按需执行的设备叶子功能 |
| `nl.py` | newLISP screen 会话 | `nl.py` | 保留为按需启动的设备叶子功能 |
| `op.py` | 管理员列表、权限检查和 `.op` | `op.py`；`_code/funcs.py` 的 `check_op_and_reply` | 保留；`PHASE = INFRA`，身份初始化后读取 ops |
| `pctest.py` | 彩蛋命令 | `pctest.py` | 保留为叶子功能 |
| `post.py` | 测试出站端口映射 | `post.py` | 保留；修正群/私聊映射缺陷但不扩大测试权限 |
| `reboot.py` | 保存发起位置并以 233 退出 | `reboot.py` | 保留；启动问候移入 `on_load` |
| `setu.py` | 图片 API | `setu.py` 和旧设备 `pyload.py::setu` | 保留；网络访问只发生在调用时。link helper 当前会改写图片代理 URL 并可能丢失多页页码，语义未定，本轮不改 |
| `shutdown.py` | 保存发起位置并正常退出 | `shutdown.py` | 保留；下次启动问候移入 `on_load` |

`ba_gacha.py` 只是未完成占位，`test.py` 只是开发期占位；两者不建立目标模块。`.py`、`.link`、`.file` 和 `.chat` 已在前表说明，不在本表重复。

## 设备与动态叶子模块

这些代码不定义主路径，主要服务设备功能或动态执行环境。当前保留决定来自实际命令、chat 工具和 live link/pyload 消费者；它们仍是普通 mod，失败不会改变 loader。

| 目标模块 | 当前内容 | 当前来源 | 判断 |
|---|---|---|---|
| `randoms.py` | `getran`、骰子 `rd` 等随机小函数 | `_code/funcs.py` | 保留；普通命令和 live link 都有消费者，但不是基础设施 |
| `lunar.py` | `lunar_time`、`小六壬` | `_code/funcs.py` 的农历段 | 保留为独立纯函数 mod 并显式导出到 `.py`；不继续堆入 `chat.py` |
| `attrs.py` | 动态添加属性 | `_code/s3/add_attr.py` | 保留为显式动态工具，不由核心模块调用 |
| `params.py` | 函数参数预置/变换 | `_code/s3/params.py` | 保留为显式动态工具；普通模块不为它制造依赖 |
| `src.py` | 运行时读取/替换对象源码 | `_code/s3/src.py` | 保留为 op 信任域内的动态工具 |
| `petpet.py` | 本机 petpet 服务调用与别名 | `_code/funcs.py` 的 `petpet*` | 保留；live link 仍调用 |
| `game2048.py` | 2048 移动和随机生成 | `_code/funcs.py` 的 `fills` 至 `d2048` | 保留；live link 仍调用 |
| `latex.py` | LaTeX 转图片 | `_code/funcs.py` 的 `latex2img` | 保留为显式动态工具 |
| `image_tools.py` | GIF/图片缩放 `deal_img` | `_code/funcs.py` 的 `deal_img` | 保留；live link 仍调用 |
| `search.py` | `.search` 当前窗口日志正则搜索，命中整条记录、5 条交互翻页 | 旧 `search` link 的 shell `grep` + `prints(..., 5)` | 直接升格为命令；读取由 `chatlog.search_current()` 持有，不再保留 `/search` link |
| `weather.py` | 和风天气鉴权、城市/经纬查询与预报 | `_code/funcs.py` 的 `geocode`、`_require_env` 至 `get_hourly_forecast` | 保留；chat 工具和 live link 仍调用 |
| `dao.py` | 《道德经》静态文本 | `_code/funcs.py` 的 `道德经` | 保留；live link 仍调用 |
| `screen.py` | GNU screen 操作 | `_code/s3/linux_screen.py` | 保留；`nl`、MC 和 live link 仍使用 |
| `mcrcon.py` | RCON 协议 | `_code/s3/mcrcon.py` | 保留；随 MC 叶子能力使用 |
| `mc.py` | RCON 连接对象 | `_code/s3/mc.py` | 保留；设备脚本和 live link 仍使用 |
| `minecraft/` | MC 私有设备配置、惰性 RCON、screen 生命周期、link 控制边界 | 旧设备 `pyload.py` 的 MC 段 | 文件夹 mod；设备 JSON 不入版本库，Import/Load 不连接 RCON，控制 helper 强制 op |
| `portfunc.py` | 加密远端函数协议、client/server/key 生成 | `_code/portfunc/` 与 `_code/s3/aes.py`、`_code/s3/rsa.py` | 保留为设备叶子协议；不持有特定远端实例 |
| `server.py` | `% ...` link 的惰性 RSA 远端函数客户端 | Git 历史 `_code/portfunc/cilent.py::connect.call()` 与持久 `server.call(...)` 消费者 | `data/device/server.json` 持有地址、端口和私钥路径；首次调用才连接，调用串行化，退出关闭 |

本次架构审核与迁移已在维护者明确授权下检查当前 `pyload.py`、实时 link 和 Git 历史。MC、瓶子游戏、编码/时间、screen、petpet、2048、图片工具、天气和远端函数客户端等稳定能力已由明确 mod 持有，`py.py` 只维护平铺名称映射。设备地址、账号和凭据只进入忽略的 runtime 文件，不进入本文或版本库。

## `funcs.py` 拆分索引

| 当前范围 | 目标去向 |
|---|---|
| 15–23：link 捕获类型 | `link` 的执行环境固定名称 |
| 27–58：当前消息匹配、日志、send/recv | `history.py`、`message.py` |
| 60–90、123–164：用户/群 storage、名字、头像、成员 | `identity.py` |
| 93–121、166–178：小工具、HTTP、随机 | `text.py`、`randoms.py`；HTTP 调用回到实际消费者 |
| 182–197：op 与聊天空间 | `op.py`、`chat.py` |
| 203–238、418–456：聊天消息转换、@ | `identity.py`、`chat.py`；无消费者的 `msgtext` 删除 |
| 458–478：临时 notice 调试与农历工具 | `shownotice` 调试节点删除；农历/小六壬 → `lunar.py` |
| 241–416：旧图片缓存和重复 `msg_split` | 删除，由 `image` 与当前 chat 实现覆盖 |
| 482–525：petpet/geocode | `petpet.py`、`weather.py`；两者都有 live link 消费者 |
| 527–602：`准6` 与 2048 | 2048 → `game2048.py`；storage-backed `准6` → `py.py` 的显式动态导出 |
| 608–619：frame locals hack、聊天/免日志群 | frame hack 删除；群配置分别归 `chat.py`、`chatlog.py` |
| 623–628：时间工具 | `chat.py` |
| 632–815：LaTeX、交互进程、图片缩放 | `latex.py`、`cpp.py`、`image_tools.py` |
| 817–945：天气 | `weather.py`，chat 工具和 live link 仍使用 |
| 948–953：重复 token 计数 | `llm`，只留一份 |
| 955–958：《道德经》 | `dao.py`，live link 仍使用 |

反向的现有文件覆盖核对见[源码覆盖索引](source-coverage.md)。
