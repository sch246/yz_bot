# 运行边界与交互测试

## 当前部署

当前设备上，柚子通过 NapCat/QQ 的 OneBot HTTP 接口运行。正式代码入口已于 2026-07-29 切换到仓库根的 module 架构；切换完成时 Bot 保持停止，需由维护者显式启动：

```text
QQ / NapCat
  ├─ 127.0.0.1:5700  接收 Bot 发出的 OneBot API 请求
  └─ POST 到 127.0.0.1:5701  推送消息和通知事件
                         ↓
                    run.py -a
                         ↓
                     main.py
                         ↓
                       mods/
```

NapCat 的 API 默认位于 `5700`，Bot 的事件监听默认位于 `5701`。端口可通过 `run.py -q/-p` 调整；启动前应确认没有另一 Bot 进程占用同一账号、运行数据或监听端口。

当前部署以 Linux 为主，聊天 shell、GNU screen、固定解释器路径和部分编译命令也直接依赖 Linux 环境。仓库根的 `.gitattributes` 使用 `* text=auto eol=lf`，因此所有被 Git 识别为文本的追踪文件都以 LF 入库和检出；即使从 Windows 操作，也不应重新提交 CRLF。二进制文件仍由 `text=auto` 排除在换行转换之外。

`run.py` 是生命周期监督者：子进程以特殊退出码请求重启，或在 `-a` 模式下异常退出时自动拉起。SIGINT 会转发给子进程，使退出保存器有机会落盘。

环境由 `pyproject.toml` 和 `uv.lock` 锁定。常用启动形式是从仓库根执行 `uv sync --frozen`，再执行 `uv run --frozen python run.py -a`；`-l/--log-only` 只记录事件，`-q` 指定 OneBot API 端口，`-p` 指定事件监听端口。离线检查命令及退出码含义见下节。

首次没有 `config.json` 时，Bot 会在终端输出四位验证码，等待某个私聊用户回传，以此设为首位 master；随后继续在私聊中设置 Bot 昵称。正常接通时终端会依次出现“连接完成”“加载完成”和账号启动信息。这个流程会读取真实 NapCat 事件，不能在生产账号上随意重演。

## 离线检查命令与结果判读

三个入口验证的范围不同，不能只看退出码并互相替代。

### `run.py --check`

```bash
uv run --frozen python run.py --check
```

它读取并编译 `mods` 下的 Python 文件，同时检查公开单文件 Module 与同名 package 冲突；不会 import `main.py`/`mods`、不会执行生命周期、不会读取运行数据或绑定端口。退出 0 只证明这层静态结构通过。

### `run.py --smoke`

```bash
uv run --frozen python run.py --smoke
```

它创建临时运行目录和假 OneBot API，然后真实执行 Module Import、Load 与逆序 Exit。loader 的成功条件是六个 required core——`bot`、`command`、`connect`、`context`、`message`、`storage`——全部可用；其它 Module 失败会被记录和隔离，但不会令 smoke 返回非零。这与生产启动允许可选功能降级的契约一致。

终端中的 `smoke required core loaded; optional failures do not fail this check: X/Y` 表示：`Y` 个 Module Import 成功进入 `ctx`，其中 `X` 个完成 Load 进入 `available`。它不表示仓库总 Module 数，也不承诺 `X == Y`。后续 `optional import failures` / `optional load failures` 才说明缺失功能。

临时 fixture 目前只提供最小 config/pyload，不复制设备配置。例如缺少 `data/device/minecraft.json` 时，`minecraft` 会失败，并可能使依赖其动态导出的 `mcf`、`py`、`link`、`later`、`todo` 连锁不可用；只要 required core 仍成立，退出码仍为 0。这证明核心闭环可启动，不证明这些设备功能失败，也不能据此判定生产迁移失败。

若当前任务要求“所有 Module 都成功”，不能把现有 smoke 的退出 0 当作该断言；应检查失败列表，并为目标可选功能提供脱敏的临时设备 fixture，或另建明确的严格验证入口。

### `run.py --smoke-migrated`

该模式使用仓库根作为 runtime root，只把 OneBot API 换成假的。Module 仍会读取当前 config/storage/cache，并可能在 `on_exit()` 保存它们，所以它不是无副作用的临时 smoke。只有在 Bot 已停止、维护者明确授权使用当前运行状态时才能调用；日常静态检查优先使用 `--check`，核心生命周期检查使用临时 `--smoke`。

## 源码语法检查与动态源码警告

检查追踪 Python 源码时应读取文件并调用 `compile()`，或执行 `uv run --frozen python run.py --check`。import `mods` 本身不再触发加载，可以安全地 import 子模块调用纯函数；但仍然不要 import `main.py` 或调用 `mods.boot()`，那才是真正绑定监听端口、起调度线程、载入真实 storage 并注册退出写入的一步。当前追踪源码可以在把 `SyntaxWarning` 提升为错误的条件下完成纯编译；正则、替换模板和命令匹配中的反斜杠应使用 raw string 或双反斜杠表达。

终端输出按行原子：`mods.log` 在加载时包装 `sys.stdout`，让正在写半行的线程独占终端并继续直写——模型回复仍然逐字出现——其余线程写完整的行先排队，等这一行结束再输出；日志的 console handler 共享同一把锁。因此多线程（监听、聊天、子任务、storage 同步）不会再互相插进对方的半行里，代价是后台的行可能稍晚出现，且排在它们到达时那条回复之后。持有终端是一份由每次写入续租的租约（`mods.log.LINE_LEASE`，默认 5 秒）：写得慢但还在写的持有者一直续租、保住整行，卡住不写的等租约到期就断行让路，回复在下一行继续；持有者中途死掉则立即让路。持有者按线程对象而非 `get_ident()` 记录，因为线程退出后 id 会被复用，否则新线程会被当成旧线程续写它遗留的半行；退出时会把没有换行的残留补完。

终端输出按流分组：产出者不再直接 `print`，而是选一条流名（`mods.log.stream("msg")` 即 logger `yz.msg`），由终端决定看不看。当前的流有 `yz.msg`（收发消息）、`yz.llm`（视觉准备、等待、失败提示）、`yz.llm.request`（整条请求栈）、`yz.llm.message`（非流式的完整消息与工具结果）、`yz.image`、`yz.storage`、`yz.link`、`yz.agent`、`yz.mc`、`yz.jm`、`yz.py`、`yz.scheduler`。终端的实际内容是「已订阅流的 INFO 及以上」∪「所有 logger 的 WARNING 及以上」——退订只减少 INFO 流量，永远不能让故障静音，也不影响落盘：无论是否订阅，记录都照常写进那条流自己的文件。订阅集用 `.log` 命令改（见[功能与命令目录](commands.md)），存在 `data/storage/log/terminal.json`，重启不重置，默认 `["yz"]` 即全订，与迁移前的终端一致。

三个落盘出口各管一件事：`log/<流>.log` 是每条流自己的证据，按顶层流名一个文件（`yz.llm.request` 和 `yz.llm` 同写 `log/llm.log`，子流是订阅粒度不是文件粒度，完整 logger 名在每行上可以 grep），第一次产出时才建，午夜轮转保留 7 天，`tmux` 开窗口 `tail -f log/llm.log` 就是在看一条流；`app.log` 回到只记严重程度——所有 logger 的 WARNING 以上，加上非流 logger 的 INFO 以上；`chatlog` 仍是产品聊天历史。`log/` 与 `app.log*` 都已在 `.gitignore` 内。

落盘的每行都带归属：一个 filter 在写文件前从 `context` 读当前事件，群聊补 `[g<群号> u<QQ>]`、私聊补 `[u<QQ>]`，没有当前事件就什么都不补（行的形状与之前一致）。**没有任何调用点需要传参**，现有和以后的日志调用都自动带上，这也是它做成 filter 而不是参数的原因。终端不显示归属：终端是状态面，保持产出者自己选的形状。

LLM 的逐字输出不是流，而是终端的交互效果：它仍然直写 `sys.stdout`，因此 `.log` 退订 `yz.llm.request` 只会让请求栈消失，模型仍然逐字出现在终端。`main.py` 的启动/退出摘要和 `identity` 的首次配置验证码同样不走流——恢复入口不可被订阅集关掉。

终端中的警告位置需要区分：`某文件.py:<行号>` 指向普通源码；`<string>:1: SyntaxWarning: invalid escape sequence '\d'` 表示 `eval()`/`exec()` 正在编译动态字符串。后者可能来自聊天中的 `.py`/LLM 工具参数、link action，或 `data/pyload.py` 等以字符串方式执行的设备级源码，不能据此反推追踪文件仍有同一问题。`mods/tools/<name>.py` 则以真实文件名编译；其语法、顶层执行或 schema 校验错误会在 `reload_tools` 结果和应用日志中保留完整 traceback，旧 last-good 版本继续服务。正则中的数字模式应写成 `r'\d'`，需要普通字符串反斜杠时写成 `'\\d'`。

`.jm` 插件仍会在加载时导入 `jmcomic`，但站点 client 已延迟到第一次 `.jm search` 才初始化。因而启动期不应再仅由该插件打印站点域名刷新日志；实际使用搜索或下载时，第三方库仍可能访问网络并输出自己的日志。

## 真实事件路径

1. NapCat 把 OneBot 事件 POST 到监听端口。
2. HTTP 接收器解析事件并立即返回 200。
3. `mods.bot.recv()` 记录当前消息和聊天日志。
4. reply/开头 at 投影（不改写事件）、`^C` 删除 catch、延续式阻塞、普通命令、shell、link 按[完整入口优先级](interaction-model.md#入口有优先级)处理。
5. 回复进入异步发送队列，再调用 NapCat 的 OneBot API。
6. Bot 查询刚发送的消息并写入自己的聊天记录。

HTTP 200 只表示事件已被本地监听器接收，不表示命令或回复执行成功。

## 已存在的“交互端口”

端口 `5701` 本身就是可注入 OneBot 事件的接口。构造与 NapCat 相同形状的 JSON 并 POST 到它，理论上可以从聊天外测试完整反应路径。

监听器只绑定 `127.0.0.1`，但不校验 HTTP 路径、方法或鉴权；任何能访问本机端口并提交可解析 JSON 的进程都能注入事件。本机边界就是它目前的信任边界。

但当前监听器没有测试命名空间或 dry-run：注入事件会被当成真实消息，可能写入聊天日志、修改 storage、执行 link action、调用 LLM、发送 QQ 消息，甚至触发宿主机管理能力。因此不要把正在运行实例的 `5701` 当作无副作用测试 API。

代码内还有 `recvmsg()`，可从 `.py` 或 link action 递归构造一条消息进入 `mods.bot.recv()`。它同样走真实状态和真实副作用，只是省略了 NapCat 入站网络。


## `!` 命令行入口

当前 Bot 的命令行入口就是 `mods.bot` 中的 `!` 分支。管理员发送 `!<命令>` 后，`_run_bash()` 在宿主机启动 shell 子进程并把输出发回原聊天；`#!<命令>` 只回报“将执行什么”，不实际启动进程。

因此聊天中的 `!` 可以启动任意已经存在的命令行辅助程序，但 `!` 分支本身不实现虚拟群聊切换或发送截获。

## 组合式交互测试

维护者记忆中的交互调试设计由两部分组成：向 `5701` 注入虚拟事件，再用 `.post` 把该聊天窗口的默认出站端改到假 OneBot API。其设计流程是：

1. 在测试端口启动一个假的 OneBot API 接收端；
2. 向生产进程的 `5701` POST 一条形状完整的虚拟群聊或私聊事件；
3. 让该虚拟窗口发送 `.post <测试端口>`；
4. `.post` 把 `g<群号>` 或 `u<用户号>` 写入内存 `post_map`；
5. 此后该窗口产生的 `send_msg` 和 `get_msg` 请求应不再去 NapCat `5700`，而是进入假接收端；
6. 继续向 `5701` 注入消息，即可观察真实命令、阻塞和 link 反应，同时截获默认回复。

假的 API 端不能只记录 `send_msg`：当前 `mods.message.send()` 在发送成功后还会调用 `get_msg` 取得刚发送的完整消息并写日志。测试端至少要为这两个 action 返回符合当前代码预期的 `retcode`、`data.message_id` 和消息 `data`。`.post <端口>` 的确认回复本身就会走新映射，所以假 API 必须先启动。

当前窗口使用 `.post <端口>` 不要求 op；用 `.post g<群号>`、`.post u<用户号>` 操作其它窗口或用 `.post *` 清空全部映射需要 op。对当前窗口发送不带端口的 `.post` 可恢复默认路由。映射不持久化，进程重启后自动消失。在群里，“当前窗口”是整个群，因此任意成员当前都能改变群共享映射。

群聊与私聊映射现在分别只选择 `g<群号>` 或 `u<用户号>`，不会再由 `user_id=None` 覆盖已经选中的群端口。但这只修复出站选择，不改变下述真实状态副作用和权限边界。

这套方式隔离了 QQ 出站发送，却没有隔离 Bot 本体：虚拟事件仍会进入真实 chatlog、cache、storage、计划任务、LLM 和宿主机 action。测试应使用专用虚拟 ID、受控消息和假 API 端，不触发 `.py`、shell、服务器控制等高权限路径。

## 传输边界

当前只支持 `mods.connect` 的 OneBot HTTP 路径。go-cqhttp 时代的 WebSocket connector 已随旧 `_code` 实现退出正式代码树；相关演进只保留在[历史文档](history.md)，不能作为可选运行入口。

## 推荐的测试层次

### 观察测试

在真实 QQ 测试群中发送消息，观察回复、日志和状态。这最接近真实行为，但只适合低风险反应，并应使用专门群和测试账号。

### 隔离的端到端实例

若要测试 NapCat 事件：

1. 使用不同的监听/API 端口；
2. 使用临时工作目录或独立的 `data`、`chatlog`、配置文件；
3. 禁止连接生产 Minecraft、远程 shell、LLM 计费接口等能力；
4. 用录制并脱敏的 OneBot JSON 作为输入；
5. 用假的 OneBot API 接收端记录 Bot 想发送的请求。

仅修改 `-p` 启动第二个进程并不够，因为两个实例仍可能共享 storage、日志、配置和宿主机资源。

### 纯反应测试

长期最安全的测试接口应只接收“事件 + 初始状态”，返回“命中的路由 + 计划发送的消息 + 状态变化”，不绑定 socket、不启动 scheduler、不写真实文件。这目前还不存在；在它出现前，文档中的 link 快照和 `.link catch` 只能辅助理解，不能替代无副作用验证。

现有 `5701 + 私聊 .post + 假 OneBot API` 可手工组成真实集成调试通道，但仓库目前没有配套假 API 脚本、脱敏 fixture 或自动清理步骤；它不是已经建立的测试套件。纯反应测试仍有价值，因为它可以连 chatlog/storage 等入站副作用也一起隔离。

## 生命周期细节与常见故障

- `.reboot` 只有在外层 `run.py` 监督时才会重新启动；直接运行 `main.py` 会以 233 退出后停住。
- `.shutdown` 以 0 正常退出，所以即使开启 `-a` 也不会被当作异常重新拉起。
- 终端第一次 `Ctrl+C` 会让 `main` 进入显式退出路径；父进程只转发第一次 SIGINT，子进程忽略保存期间的重复 SIGINT。退出顺序固定为等待 scheduler 任务结束、停止 storage watcher/worker、强制保存全部 storage；各 shutdown 仍注册为幂等 `atexit` 兜底。`SIGKILL`、断电和 Python fatal error 不经过这条路径。
- 退出前的“重启中/关闭中”会等待发送 Future 完成；下次启动的“重启完成/醒了”只排入发送队列，钩子文件随即删除，不保证 NapCat 已确认送达。
- 反复打印连接错误通常先检查 NapCat 的 API 端口；监听端口被占用会在导入 HTTP 连接器时直接失败。
- 某个命令导入失败会打印 traceback；若由 `.reboot` 发起且问候文件仍在，加载器还会把失败命令列表发回原窗口。
- 自动重启循环不等于自动修复。持续崩溃时先保存终端 traceback，避免通过高风险聊天入口继续扩大状态变化。

## 图片缓存硬切迁移

图片缓存从“完整 URL 的 MD5 文件名、完整 URL 的描述键”直接切换为内容摘要身份：原图统一使用 SHA-256，迁移时也可从部分 QQ URL 恢复仅供描述命中的 SHA-1/MD5。运行时不保留 URL 键回退。部署包含这项变更的代码前：

1. 停止 Bot，避免迁移脚本与 storage 同时写入；
2. 运行 `python3 scripts/migrate_image_cache.py` 做只读报告；
3. 没有缓存原图、URL 自身也不携带可恢复内容摘要的旧描述会按主机聚合报告，并默认阻止迁移；审核后可用 `--drop-unresolved` 明确丢弃。同一内容的多个描述按日期选择最新，同日期按旧 JSON 中后出现者覆盖；只有 URI alias 指向不同 SHA-256 时才默认阻止，可用 `--force-conflicts` 保留先遇到的映射；
4. 加 `--apply` 执行迁移，再启动新代码。

脚本不会下载已过期 URL，也不会把 URL、签名参数或描述正文打印到报告。执行前会把相关 JSON 和待改名图片用硬链接（不支持时复制）备份到 `data/migrations/image-cache-<时间>/`，并写入文件改名清单。迁移脚本属于离线部署步骤，不能在正在运行的 Bot 上调用。

## 手工编辑 storage 文件

`data/storage/**.json` 是内存的磁盘投影，也是有意保留的维护入口：改文件是修数据的正常
手段，不是只能读的转储。同步由 `mods.storage` 的后台 worker 完成（watchdog 事件，
watchdog 不可用时退回 stat 轮询），因此改动通常在一两秒内生效，不需要重启。

除了直接改字段值，文件内容本身有两个约定：

| 文件内容 | 效果 |
|---|---|
| 合法 JSON | 按下表的冲突规则覆盖内存 |
| 整个文件为空 | 从内存回写重建这份文件 |
| 整个文件只有 `DELETE` | 删除这一项的内存与文件 |

空文件是「改坏了还原不回来」的退路：清空文件，下一轮同步就按内存里的样子重写一份。
`DELETE` 则是手上只有编辑器时删掉一整项的办法。两者都作用于单个文件对应的那一项。

冲突以内存为准，但只在内存确实变过时：

- 文件变了、内存没变 → 文件生效，且运行中的功能立刻看到（容器是原地重填的，不是重新
  绑定，所以持有引用的模块不会看着旧对象）；
- 文件和内存都变了 → 以内存为准，文件会被回写覆盖，并打印一条 `文件覆盖内存失败`；
- 文件被删除 → 保留内存，等待下一轮重建；
- 文件不是合法 JSON → 不替换内存，记录错误。此时若内存里也没有这一项，`storage.get()`
  会抛异常而不是返回空默认值——否则下一次保存就会把损坏文件覆盖成空的，把「损坏但内容
  还在」变成「内容真的没了」。

只改缩进或键的顺序不算变化：变化检测比较的是规范化（排序、紧凑）后的内容，不是字节。

无法 JSON 化的值会被写成 `null`（非基本类型的键会被整个丢弃，数字键会被转成字符串），
这是为了不让一个坏值卡住整次保存。它不再是无声的：真正落盘时会打印一条
`有值无法序列化，已写成 null`。但磁盘与内存的 digest 仍然相同，所以同步逻辑不会把它
当成冲突——发现它只能靠这条日志。

## 未实现的运行提案

Mods 两阶段加载已经成为当前架构。多项目共存、storage 值校验与历史恢复、异常 incident/节流等剩余讨论统一收在[工作提案索引](working/proposals/README.md)。Storage 文件热同步已经实现并记录在[运行架构](architecture.md#storage-与设备状态)。

## 运行数据边界

以下目录或文件属于真实运行状态，不是测试夹具：

- `data/`：storage、`pyload.py`、临时文件和功能数据；
- `chatlog/`：真实群聊和私聊记录；
- `config.json`、`.env*`、密钥文件：账号、服务和模型配置；
- `outputs/`、`woutputs/`：模型或 RAG 输出；
- 仓库内虚拟环境目录。

功能文档只说明它们的角色，不复制其中的账号、群号、远程地址、密钥或聊天内容。

本次调查在被忽略的设备级/遗留脚本中观察到了明文凭据形态的值。文档没有复制这些值，也没有修改正在运行的配置；仍有效的凭据应当迁移到环境变量或独立配置并轮换。
