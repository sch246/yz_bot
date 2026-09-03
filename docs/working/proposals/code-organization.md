# 新代码架构与一次性切换

> 状态：已于 2026-07-29 完成直接切换；本文作为决策与迁移记录保留
>
> 最近更新：2026-07-27

本文记录从旧 `_code/main.py`、`funcs.py`、`bot/`、`s3/` 和命令扫描器一次性切换到 `mods` 系统的目标与取舍。仓库根 `run.py → main.py → mods` 现已是唯一正式路径；当前事实以[运行架构](../../architecture.md)为准，用户可见语义以[交互模型](../../interaction-model.md)为准，loader 规则见 [Mods 两阶段加载](mods-loading.md)。

这不是把旧目录搬到新名字下，也不建立传统分层框架。目标是让普通 Python 模块直接组合，消除部分初始化的 `main`、`from funcs import *`、多份动态 globals 和 import 副作用，同时保留柚子作为个人操作环境的直接能力、持久状态和恢复入口。

## 已作出的架构决定

- 新系统只有一条启动、加载、路由、保存和退出路径；切换时不保留旧 loader、旧 `main` 出口或新旧双注册。
- OneBot 事件继续使用原始 `dict`；不会引入消息 DTO、事件 class 层级或统一业务对象。
- 所有功能都是 `mods/` 下平等的普通模块。目录分组只用于阅读，不引入 core/service/adapter 等层级。
- 稳定依赖使用普通 import；只有 `.py`、link 和诊断工具取得完整 `ctx`。
- 命令继续保持普通函数加返回值/generator 的低样板形态；无参数 `@command` 从模块名和函数名推导入口，不建立命令基类。
- 生命周期只有 `Import → Load → Run → Exit`，Load 只有 `INFRA → FEATURE → LATE` 三个阶段。
- import 和 `on_load()` 失败会被记录并隔离；失败模块不作为可用命令或运行入口，但仍可在 `ctx` 中被管理员观察。
- storage、scheduler、listener、后台任务、启动问候和外部 client 不得在 import 时启动。
- `.py` 与 link 共享一个执行环境。模块名和兼容的平铺词汇都由版本控制内的显式导出表提供，不再由星号导入偶然形成。
- 修改源码后仍通过完整进程重启切换版本；不提供通用热重载。
- 当前运行数据格式和路径在架构切换中保持不变，使回滚不依赖反向数据迁移。

## 必须保留的行为

新结构必须保持下面这些事实，而不是逐行复制旧实现：

1. 明确命令、shell、取消和维护入口不能被宽泛 link 覆盖。
2. 同一交互线的 generator/`.py input()` 可以等待下一条消息；群内不同用户互不抢占。
3. LLM 上下文按群窗口或私聊窗口共享，不改成每用户独立会话。
4. 用户、群、窗口、全局 Bot、持续任务和单次交互继续拥有不同状态范围。
5. `.py`、link、文件、shell、进程和重启仍是 op 信任域内的正式能力。
6. storage 返回可原地修改的普通对象；内存、磁盘和人工强制加载的权威关系保持清楚。
7. chatlog 继续实时追加，storage 和计划任务在正常退出时完成收束与保存。
8. `.reboot`/`.shutdown` 的跨进程问候和 `run.py` 的监督语义继续存在。

已经确认的实现缺陷不作为兼容目标：失败命令仍占用入口、`^C` 不唤醒 `.py input()`、群聊 `.post` 映射被 `uNone` 覆盖、link action 异常仍进入 `succ`，都应在新路径中直接消失。

## 最小运行闭环

```text
run.py 监督进程
  → main.py 安装信号处理并 import mods
  → mods 完成 Import 与 Load
  → bot.run() 从 connect 接收原始事件
  → context 建立本次执行位置，chatlog/history 记录事件
  → continuation / command / shell / link 按固定顺序消费
  → 普通模块产生发送、状态变化、任务或文件操作
  → 退出时 mods.exit() 逆序关闭并保存
  → 233 由 run.py 拉起全新进程
```

这条闭环只要求模块、事件位置、稳定入口、动态执行、持久化、持续任务和维护能力。LLM、图片、Minecraft、娱乐命令和外部服务都是叶子能力；它们可以失败或删除，但不能改变 loader 和主路由的形状。

## 目标目录

```text
run.py                   # 进程监督，保留现有 CLI
main.py                  # 信号、import mods、run/finally/exit
mods/
  __init__.py            # Import、ctx、Load 排序、可用表、exit
  bot.py                 # 唯一消息循环和固定路由顺序
  command.py             # 无参数命令注册、匹配、帮助和可用性过滤
  capture.py             # Load 激活的进程内通用捕获声明
  connect.py             # OneBot HTTP 入站与 API 调用
  context.py             # 当前事件、交互线和可取消 continuation
  history.py             # 近期事件窗口
  identity.py            # Bot/用户/群资料与名字
  message.py             # send/sendmsg/recvmsg 与发送节流
  msgs.py cq.py chatlog.py pages.py text.py
  config.py scheduler.py thread.py log.py
  storage/               # __init__ 持有状态权威；codec 是纯投影格式
    __init__.py codec.py
  py.py later.py todo.py
  link/                  # 线性持久反应与进程内 capture 调度
    __init__.py schema.py
  image/                 # 内容缓存入口；身份与视觉切片在内部聚合
    __init__.py identity.py vision.py
  llm/                   # 模型执行入口；模型目录、工具和值类型在内部聚合
    __init__.py models.py tools.py types.py
  chat.py
  file.py dir.py edit.py op.py reboot.py shutdown.py
  ...                    # 普通命令和设备/娱乐叶子模块
```

`mods/` 的公开 Module 保持平级，但可以采用且只能采用一种形态：小 Module 使用 `name.py`，需要内部 Locality 的深 Module 使用 `name/__init__.py` 与同目录内部文件。同名文件和 package 不得并存；loader 只 Load 顶层公开 Module，不递归把内部文件变成独立生命周期。package 内命令统一归属顶层 Module，因此整个 package 仍只有一个可用性和失败域。

文件夹只表达一个已经存在的公开概念内部有多套共同维护的机制，不能仅为分类预先建立 `infra/`、`services/`、`features/` 或总 `commands/` 层。完整职责表见[模块手册](module-manual.md)，当前源码的反向去向见[源码覆盖索引](source-coverage.md)。

## 依赖规则

普通模块只依赖自己实际使用的模块：

```python
from mods import storage
from mods.message import sendmsg
```

以下写法在新代码中不存在：

```python
from main import ...
from main import *
from funcs import *
```

普通 import 表达名称依赖，不自动表达启动顺序。只有 `on_load()` 消费另一个模块初始化出的运行值，或退出顺序需要约束时，才声明 `LOAD_BEFORE` / `LOAD_AFTER`。不为“看起来相关”建立生命周期边。

`main.py` 不是 service locator，也不重新导出 `mods` 名称。`mods.__init__` 只承担模块发现和生命周期，不代理业务调用。`bot.py` 明确 import 路由所需的少数模块；其它功能不能反向 import `bot` 来取得公共词汇。

## Import、Load 与 Exit

### Import

`mods/__init__.py` 按名称排序扫描非下划线 `.py` 和包含 `__init__.py` 的同级 package，使用正常 Python import；发现同名两种形态直接报错。Module 顶层只允许定义函数、类型、常量、锁、内部 import 和注册项；不读写运行文件、不访问外部服务、不创建 socket、线程、scheduler 或子进程。

Import 失败会记录模块名和完整异常，继续导入其它模块。失败模块不进入 `ctx`；command/capture 已产生的声明按函数的 `__module__` 归属过滤并清除。capture 只有在所属模块成功 Load 后才激活。

### Load

所有 Import 完成后建立 `ctx: dict[str, ModuleType]`，再一次性计算完整加载顺序。阶段固定为：

```text
INFRA → FEATURE → LATE
```

- `INFRA`：listener、storage、日志、scheduler、近期历史、身份与 op 等运行基础；
- `FEATURE`：普通命令、LLM、图片和其它叶子能力，默认阶段；
- `LATE`：必须看到完整模块世界的 `.py`、link、later/todo 恢复。

未知阶段、阶段逆向依赖和同阶段循环在调用任何 `on_load()` 前直接使整体启动失败；指向 Import 失败模块的关系只记录警告并忽略。单个 `on_load(ctx)` 失败不回滚其它模块，但该模块不进入 `available`。

没有 `on_load()` 的模块在排到自身时成为可用。命令注册项记录自己的所属模块，帮助与分发只查看 `available`，因此失败功能不会继续占住命令名。

loader 完成后核对普通常量集合 `REQUIRED_MODULES = {"bot", "command", "connect", "context", "message", "storage"}`。这些模块任一未 Import 或未 Load，启动在打印完整汇总后失败；其它模块仍按可选失败隔离。这个集合只定义最小运行闭环，不引入模块等级或依赖容器。

### Exit

`mods.exit()` 只遍历成功成为可用的模块，按相反顺序调用存在的 `on_exit()`。一个退出钩子失败只记录异常，不能阻止其余保存与关闭。主要顺序由依赖自然反转：先停止后来恢复的任务和外部入口，再关闭 scheduler/listener，最后保存 storage 与日志。

这套钩子只处理完整进程生命周期，不承担模块热重载。

## 事件路由

`bot.recv(event)` 保持可直接阅读的显式分支，不建立通用 handler 总线。顺序固定为：

1. `None`、心跳和输入状态提前结束；
2. 设置当前事件，写 chatlog 和近期 history；log-only 在此停止；
3. 文本事件规范化 reply CQ；
4. `^C`/`^c` 取消同一交互线上的等待；
5. 仍存在的 continuation 消费整条事件并结束本次路由；
6. 已注册且可用的点命令；
7. `!` shell；
8. `#!` shell dry-run；
9. 可用的 link 顺序节点，未匹配时再尝试进程内 capture；
10. notice 先处理撤回，再交给 link。

`bot` 不能在入口处硬取 `py` 或 link。它只在对应步骤查询模块是否可用；因此 `.py`/link 加载失败时，明确命令、文件、重启和其它恢复入口仍然工作。

未知点命令继续允许落入 link。Continuation 仍优先于普通点命令，只有取消入口可以抢在它前面。

## Continuation 与取消

`context.py` 用交互线 key 保存 continuation：群聊为 `(group_id, user_id)`，私聊为 `(None, user_id)`。generator 和 `.py input()` 使用同一登记表，但保留各自最小机制。

取消采用一个明确的 `InteractionCancelled` 异常：

- generator 被移出登记表，不再接收后续消息；
- `.py input()` 的等待对象被唤醒，并在调用线程抛出 `InteractionCancelled`；
- 取消消息本身继续走后续普通路由，与当前交互模型一致；
- 裸 `^` 不再作为推荐取消语法，也不承诺兼容其偶然行为。

这只修复已确认的阻塞线程缺口，不引入统一 session class 或通用交互状态机。

## 命令模型

命令模块在 Import 阶段用无参数薄装饰器注册：

```python
@command
def run(body: str):
    ...
```

`mods.bar.run` 注册为 `.bar`；`mods.bar.foo` 注册为 `.bar.foo`。package 内仍取顶层公开模块名。装饰器不接受手写名字，因此命令名始终可从源码位置和函数名推出。调用继续接收未解析的 `body`，函数可以返回字符串、`None`、Future 或 generator。`params`、`grouponly`、权限检查和分页仍是普通函数组合，不进入命令对象层级。

本次架构切换不采用尚未定案的整数权限系统。保留 master/op 信任域，并提供一个普通 `require_op(msg)` helper；所有 shell、代码、文件、link、进程、路由和服务器控制入口在覆盖审核中必须明确调用它。缺失检查不能以“以后再做权限框架”为理由保留。

## 状态与写入权威

| 事实 | 唯一写入权威 | 持久投影或派生表示 |
|---|---|---|
| 当前事件、等待下一条输入 | `context` 内存 | 无 |
| 近期窗口事件 | `history` 内存 | 退出缓存文件 |
| 完整可读聊天历史 | `chatlog` 追加写 | 按群/私聊和日期的文本 |
| 普通业务状态 | `storage` 返回的内存对象 | `data/storage/*.json` |
| master/op 与 Bot 昵称 | 切换时仍由 `config` + `op/identity` 持有 | `config.json`；后续迁移另议 |
| 用户/群资料缓存 | `identity` 内存 | OneBot 查询和 storage 名称覆盖 |
| `.py`/link 执行名字与临时值 | `py.loc` | 版本化导出表和 `pyload.py` 输入 |
| link 顺序节点 | storage 中的 links 列表 | `links.json`；每项只有 `name/type/cond/action` |
| later/todo 任务 | storage 列表 + scheduler job | 原有 JSON 形状 |
| LLM 模型与提示设置 | storage | provider client 是可重建派生对象 |

架构切换不同时迁移 config 或 storage schema，避免一次变更同时替换代码路径和数据权威。storage 继续以内存为运行期写入权威、JSON 为可编辑投影；显式 `storage.load(namespace, name)` 仍是管理员选择磁盘覆盖内存的入口。

## `.py`、link 与动态导出面

`py.py` 在 `LATE` 阶段建立唯一 `loc`：

1. 放入安全的 Python 基础名称；
2. 把所有 Import 成功模块按模块名放入，例如 `storage`、`message`、`identity`；
3. 合并版本控制内的 `DYNAMIC_EXPORTS` 平铺名称；
4. 在候选字典中执行 `data/pyload.py`；全部执行成功后才以候选字典替换正式 `loc`；
5. `link` 直接复用同一个 `loc`，不复制或定期刷新 globals。

`DYNAMIC_EXPORTS` 是动态编程界面的明确词汇表，不是旧 `main` 的兼容出口。它至少保留当前 live link/pyload 实际依赖的几类名称：

- 消息和位置：`sendmsg`、`recvmsg`、`getlog`、`msg_id`、事件谓词；
- 身份和状态：`getname`、`setname`、`getstorage`、`getgroupstorage`；
- 动态反应：`getmod`、`prints`、link 模板捕获类型；
- 当前设备能力：MC/screen、petpet、2048、图片缩放、经纬/天气和静态文本；
- `.later` 重复任务等已经由设备脚本使用的入口。

具体名称清单应写在 `py.py` 的普通字典旁，并在切换验证中和当前 live link/pyload 做一次静态覆盖核对。新增平铺名称必须有真实动态消费者；否则动态代码使用模块名访问。

`getmod(name)` 只服务模块名由运行期计算的 `.py`/link；版本控制内代码和名称已知的 link action 直接 import/引用模块。旧 `getcmd()` 返回命令模块，而新 command registry 返回命令函数，因此不保留这个一名两义的入口。

`ctx` 模块名是保留名，pyload 不得用同名普通值替换模块。平铺 alias 允许被 pyload 有意覆盖，以保留设备试验能力；启动汇总必须列出发生覆盖的名称，最终 `loc` 是运行时唯一解释结果。稳定 helper 迁入版本控制后，设备文件中的同名覆盖应在单独获得生产数据修改授权时删除，但它不是本次文档或代码切换可以静默改写的数据。

link 按同一条线性顺序检查节点：条件失败继续下一个，首个匹配节点执行 action 后结束。没有 `succ/fail/while`、反向边或递归遍历。持久节点来自 JSON 数组；其它模块可从独立 `capture.py` 使用 `@capture` 声明进程内节点，或用 `@capture(before="link-name")` 保留它在旧反应链中已证明的优先级。声明在 Import 时只收集，所属模块成功 Load 后才激活；未指定位置的 capture 排在持久节点之后，定位目标不存在时也降到末尾，且 capture 永不写入 `links.json`。capture 抛异常时记录并继续后续线性节点，不能在未成功返回命中时声称消费了消息。

re 节点 action 中的 `{:name}` 保留历史的原样文本替换，用于已经在字符串内部的片段；当捕获值要作为独立 Python 参数时使用 `{:name!r}`，它会生成安全的字符串字面量，避免中文被解释为变量名，也避免引号逃逸 action。

旧 `chatting` 和“吗？”节点由有审计锚点的 chat capture 承载；`shownotice` 只是临时 raw notice 调试反应，明确删除而不升格为 capture。`trans_img` 直接使用 `file`，`reply` 直接使用 `link.set_literal()`，不经过命令 registry 取模块。

`.search` 保留的行为边界是“当前窗口全部日志匹配→CQ 转义→每 5 行交互式翻页”；内部从 shell `grep` 改成 Python 搜索不允许顺便删掉分页、截断为固定条数或改变无结果时的分页反馈。它已从 `/search` 持久 link 直接升格为 `search.py` 命令，不保留双入口。（这条边界后来被有意改动：翻页单位从 5 **行**变成 5 **条记录**，因为按行返回会让命中正文时丢掉发送者、命中记录头时丢掉正文。分页、无结果反馈和单一入口都保留，理由见[统一消息模型](message-model.md)的 search 一节。）

LLM 聊天的 `#` 管理子命令保留旧版已注册的 model/models/use_model、prompt/add_prompt、setting/use_setting/set_setting/del_setting、image 和 help 行为。特别地，`#model <selection>` 只查询模型信息，只有 `#use_model <selection>` 修改当前窗口的模型。

`setu` 的 link helper 当前会把原图 URL 改写为代理地址，且对多页作品可能丢失页码。维护者尚未决定应保留原地址还是代理可访问性，因此本轮只记录，不静默改变它。

live link 中的 `server.call(...)` 已追溯到 2024 年 Git 历史中 `_code/portfunc/cilent.py` 的 RSA 验证长连接客户端：连接时用本机私钥签名随机数完成身份验证，`call()` 在同一 socket 上加密发送一个值并同步返回解密结果。新 `server.py` 持有这个惰性客户端并在退出时关闭；未进入 Git 的设备实例参数由 `data/device/server.json` 唯一持有，字段为 `host`、`port`、`private_key_file`、可选 `is_str` 和 `timeout`。地址与私钥不回填到源码。

`pyload.py` 的长期目标只保留设备配置和现场试验。稳定、被 live link 依赖的函数进入版本控制模块；在设备文件获准清理前，同名旧定义按上面的显式覆盖规则继续工作。凭据和机器地址进入环境变量或设备配置，文档和源码不复制其值。`###` 若继续提供持久追加，也只能追加到设备试验文件，失败内容不得污染已安装环境。

旧 `.py data` 不再由根目录 `data.json` 单独读写；新环境中同名 `data` 直接指向 storage 的 `py_data` 对象。旧文件内容不作为切换数据输入，因此不建立双写或启动 fallback。

## 持续任务与后台资源

- `scheduler.py` 只拥有一个 APScheduler 实例，不注册业务 job；
- `later.py` 在 `on_load()` 从原有列表恢复一次性任务，并通过 `py.loc` 执行；
- `todo.py` 保留，但删除当前重复/损坏解析器和永久 `while sleep` 线程，改为向 scheduler 注册一个周期 job；
- `mcversion` 等叶子模块在自己的 `on_load()` 注册 job；
- REPL、screen、RCON 和其它子进程只在实际使用时启动，并由所属模块在 `on_exit()` 关闭自己创建的资源。

后台任务不能通过 import 启动，也不能把 shutdown 责任留给零散 `atexit`。`atexit` 只保留在最外层作为幂等兜底，正常路径由 `mods.exit()` 明确执行。

## 功能保留与删除边界

为避免“重写”悄悄变成功能清空，切换基线采用保守规则：当前能加载的用户命令和被 live link/pyload 引用的设备能力默认保留为叶子 mod。语言 REPL、Minecraft、petpet、图片工具、天气/经纬、娱乐命令和外部服务不会进入核心，但在同次切换中有明确去向。

以下内容明确不进入新系统：

- 未完成的 `ba_gacha` 和开发占位命令 `test`；
- 已知失效且无其它消费者的 `kmmm` 历史图片 API；
- 未接入主路径的旧 WebSocket connector；
- 根 `_code/chat.py`、旧 `Tool` 副本和 `vendor.py`；
- 第二套 logger、旧 `sched` 实现和只服务旧 WebSocket 的标识器；
- 未接入主路径的 HippoRAG、导入脚本和试验测试；
- `funcs.py` 中被当前 image/chat 实现覆盖的重复图片下载和 `msg_split`；
- 任意 globals 容器、frame locals hack 和没有消费者的重复 helper。

其它模块的保留、合并和删除结果以[模块手册](module-manual.md)和[源码覆盖索引](source-coverage.md)为实现清单；它们不能重新改变本页的核心边界。

## 失败边界和可恢复性

| 失败位置 | 目标行为 |
|---|---|
| loader 规则无效或核心 `bot/connect/context` 不可用 | 启动失败，由 `run.py -a` 按现有策略处理 |
| 可选模块 import / `on_load` | 记录异常，模块不可用，其它模块继续 |
| 命令模块失败 | 不进入匹配/帮助；重启反馈列出失败模块 |
| `.py`/link 加载失败 | 动态入口不可用；稳定命令和维护入口继续 |
| 单条命令或消息 | 记录异常并向原窗口返回摘要，主循环继续 |
| link cond 异常 | 记录后按未匹配处理，继续下一个线性节点 |
| link action 异常 | 记录并停止本次 link；条件已经匹配，不能把可能已有副作用的 action 当作未匹配 |
| capture 异常 | 记录并继续后续线性节点；只有正常返回真值才消费消息 |
| storage 文件错误 | 保留最后有效内存，不用默认值覆盖 |
| `on_exit` 失败 | 记录后继续其它退出钩子 |

只保留一个应用 logger；chatlog 不承担异常日志。完整 incident 和聊天节流可以按[异常与日志提案](errors-and-logging.md)继续实现，但不是 loader 切换成功的前置框架。

## 一次性切换

最终采用直接切换，没有让新旧系统共享真实入口或写入权威。切换时两个 Bot 都已停止；候选的 config/storage/env、近期 cache 和新增 chatlog 被并入正式目录，旧实现及两版重点动态数据保存在仓库外的只读回滚目录。仓库根入口随后提升为唯一正式路径，旧 `_code` 和旧虚拟环境退出代码树。`/root/bot-next` 暂留为候选快照，不是可同时运行的第二生产实例。

### 实现顺序

1. 先实现 `mods/__init__.py`、`context`、`command`、`connect`、`storage`、`message` 和 `bot`，用假的 connector 与临时目录验证一条纯离线事件闭环。
2. 实现 `py`/`link` 与显式 `DYNAMIC_EXPORTS`，用单向脚本从只读源迁移 `links.json`、设备 MC 配置和现场 `pyload.py`。
3. 按模块手册实现其余保留功能；每迁入一个模块就从源码覆盖索引消掉对应旧责任。
4. 在同一个代码变更中把 `run.py` 指向根 `main.py`，删除 `_code/main.py`、`funcs.py`、旧 cmds loader、`bot/`、`s3/` 和已明确放弃的文件。
5. Bot 停止后执行离线验证，再启动唯一新路径。运行期间不向生产 `5701` 注入合成事件，不连接假装成生产的外部服务。

实现可以在分支中分步编写，但在真实启动路径上不能出现双注册或 fallback。某项旧责任只有在新路径从真实入口到可观察结果已成立时才算迁移完成。

### 数据与回滚

切换前停止 Bot 并保留旧目录作为只读回滚源。普通 storage、config、近期历史和环境变量原样复制；links 从旧图一次性展开为四字段顺序列表，并把旧 `cache/cmds/calls` 名称、shell chatlog 搜索和无边界主机调用转换到新模块；MC 设备值从 pyload 提取到忽略的私有 JSON；pyload 只保留现场值。图片/JM/临时缓存和一次性问候不复制。

若启动或离线闭环失败，停止新进程并切回旧提交即可；因为没有双写和 schema 变化，不需要反向迁移。新进程已经产生的真实聊天、任务或文件副作用不能靠代码回滚撤销，因此上线验证只使用低风险观察和维护者明确授权的真实消息。

## 验收条件

切换验收按以下条件执行；与真实 OneBot 交互有关的项目仍由维护者启动后观察：

- 所有新 Python 文件在不 import 运行入口的情况下通过严格语法检查；
- Markdown 相对链接有效，当前架构和提案边界没有混写；
- loader 的阶段、前后关系、循环、Import/Load 失败和逆序 Exit 已用一次性离线实验验证；
- 一条离线事件能经过 context、命令、generator continuation、发送捕获和状态变化；
- `.py` 能看到全部模块名和显式导出，link 与 `.py` 使用同一个 `loc`；
- 当前 live link/pyload 的外部名称全部被分类为“新导出”“设备脚本定义”或“已知缺失”，没有静默 fallback；
- 动态环境只保留 `getmod`，已知模块的 link action 不经过命令 registry 反查；
- 命令/模块覆盖索引不存在“未解释”项；保留和删除都是显式决定；
- 高权限入口逐项核对 op 检查，动态功能失败时 `.file`、`.edit`、`.py`、`.link`、`.reboot` 至少有一条可用恢复路径；
- `^C` 能释放 generator 和 `.py input()` 等待，群聊 `.post` 使用群映射；
- link action 异常会停止本次线性匹配，不继续后续节点；
- 退出顺序能等待 scheduler、停止 listener/worker/子进程并最终保存 storage；
- `run.py` 只启动根 `main.py`，仓库中不存在仍可启动旧系统的兼容入口。

## 非目标

本次切换不同时引入：

- DTO、统一 Context 参数、依赖注入容器或 service manager；
- 通用事件总线、effect AST、统一 session/state 框架；
- 多账号/多实例协调；
- 通用热重载；
- 数据库替换或 storage schema 重做；
- 新的整数权限体系；
- 对 `.py`、link、shell 和文件能力的沙箱化；
- 为将删除的旧功能建立长期测试套件。

## 文档完成状态

- [x] 从交互模型提取必须保留的行为与状态范围。
- [x] 固定平级公开 Module（单文件或同名 package）、普通 import、两阶段加载和三段生命周期。
- [x] 固定唯一事件路由、可取消 continuation 和失败模块隔离。
- [x] 审核当前 `pyload.py` 与 live link 所需的设备能力和动态名称类型。
- [x] 固定显式 `DYNAMIC_EXPORTS`，不建立通往旧 `main/funcs` 的桥。
- [x] 固定保留/删除原则、一次性切换、数据边界和回滚条件。
- [x] 建立目标职责手册与当前源码覆盖索引。
- [x] 实现新代码、完成离线验证并切换运行入口。
