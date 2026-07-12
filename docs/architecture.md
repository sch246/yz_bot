# 运行架构

本文只描述当前实现和已经成立的设计边界。用户可见语义以[交互模型](interaction-model.md)为准，部署与测试边界见[运行文档](runtime.md)。尚未实现的设计统一放在[工作提案](working/proposals/README.md)，不能当作当前契约。

## `main.py` 是人工编排的 composition root

`run.py` 监督子进程和重启；真正的 Bot 进程由 `_code/main.py` 组装。

`main.py` 依次导入基础工具、连接器、命令管理器、消息/CQ/缓存/日志、LLM 等能力，再通过 `from funcs import *` 加载通用 Bot 函数。完成登录和配置初始化后，才调用 `cmds.load()` 导入命令插件并进入消息循环。

这里没有自动依赖解析。许多模块会从尚在初始化中的 `main` 反向导入对象，所以：

- 名称必须在依赖模块导入前已经出现在 `main`；
- 命令插件必须等公共环境基本完成后才能加载；
- scheduler、socket、storage、atexit 等会在 import 时产生副作用；
- 调整 import 顺序会改变启动行为，不能视作格式整理。

这套人工编排是当前真实约束，也是主要架构成本。按纵向失败域缩小 core 的方案仍是[未实现提案](working/proposals/core-and-failure-domains.md)。

## 单实例与同步主循环

当前项目按“一个进程服务一个 Bot 账号”设计。`main`、cache、storage、link 环境、命令表和连接器都是进程级单例；本项目内部没有多账号或多实例协调。

消息接收和主路由以同步循环为中心，耗时发送、link、编译/REPL 和计划任务局部使用线程。项目没有为吞吐量引入统一 async 框架：聊天规模下的可理解性和现场修改优先于高并发性能。未接入主路径的 WebSocket 实现曾使用 asyncio。

多个完全独立 Bot 项目同时连接 NapCat 属于另一项[多项目共存提案](working/proposals/multicore.md)，不改变本项目的单实例事实。

## OneBot：数据就是数据

HTTP 连接器把 NapCat 推送的 OneBot JSON 解析为普通 Python `dict`，`main.recv()`、命令、link 和日志继续传递这份数据。`bot.msgs` 用 `is_msg()`、`is_notice()`、`is_group_msg()` 等函数式谓词检查键和值，`bot.cq` 只在需要时解析消息字符串中的 CQ 片段。

系统没有建立消息 class、通知 class 和群消息子类层级。这是应保留的边界：

- OneBot 新字段可以自然穿过系统；
- link、`.py` 和调试注入可以直接构造和检查事件；
- 小函数按需组合，不必继承框架基类；
- 调试证据接近 NapCat 原始事件。

未来做依赖反转时，目标应是替换 OneBot API、storage、时钟、shell 等副作用，并让核心匹配函数能显式接收 `msg`；不应把普通数据重新藏进 DTO/class 宇宙。

## `cmds`：极简、高权限插件系统

`_code/bot/cmds/` 下每个非下划线开头的 Python 文件都是候选插件，文件名是命令名，模块提供 `run(body)`。目录先被扫描，`cmds.load()` 再逐个导入。

插件通常 `from main import ...`，因此一个文件能以很少样板获得连接器、storage、cache、文件、线程、LLM 和 `funcs.py` 的几乎全部能力。

| 收益 | 代价 |
|---|---|
| 一个文件和 `run(body)` 即可添加命令 | 依赖没有在签名或清单中声明 |
| 插件能组合所有既有能力 | 容易依赖 `main` 的加载时机和内部名称 |
| `.py`、link、命令共享词汇 | 静态分析和隔离测试困难 |
| 不需要复杂插件框架 | `main` 必须精确维护初始化顺序 |

命令内核只负责发现、匹配和调用；参数、权限、群聊限制、分页与持久化由 `read_params()`、装饰器、谓词、`pages` 和 `storage` 等普通函数组合。这不是“缺少抽象”，而是把抽象放在可替换函数而非强制基类中。

当前加载隔离有两个缺口：失败候选仍留在 `commands`，调用时可能 KeyError；`main.recv()` 又直接取得 `modules['py']`，所以 `.py` 导入失败会影响所有事件。修复这两个缺口属于当前 bug，不代表已经存在 feature/core 降级系统。

## 轻量能力适配器

`_code/s3/` 与 `funcs.py` 通过小函数包装高频运行能力：

- `to_thread` 把同步函数变成 daemon thread，可返回 `SimpleFuture`、Thread 或 `None`；
- `screen` 把 GNU screen 会话包装成 `start/check/send/log/pop/stop`；
- `call_delay` 把普通函数变成有界串行队列，间隔可由参数计算。`send()` 使用它限制 OneBot 发送队列为 20，并在发送后随机间隔约 0.3–0.6 秒。

它们保持普通函数调用外观，但失败边界有限：daemon 线程没有统一取消/join；`screen.send()` 依赖 shell 转义、共享日志和约 0.2 秒采样；`call_delay` 超时不能终止底层线程，忽略 Future 会隐藏队列满或发送异常。

## `funcs.py`、`pyload.py` 与动态名字环境

当前名称加载链是：

1. `main` 导入 `_code/funcs.py`；
2. `.py` 插件通过 `from main import *` 取得名称并建立执行字典 `loc`；
3. `data/pyload.py` 被 `exec` 到 `.py` 模块 globals；
4. link 分派前用模块 globals 刷新 `loc`，直接 `.py` 则使用当时已有的 `loc`。

因此 `.py`/link 的运行期执行状态以 `loc` 为准；`main`/`funcs.py` 和 `.py` globals 是名称来源，`pyload.py` 是设备级持久化输入。它们是一条有同步缺口的加载链，不是几个平等真相源。

`_code/funcs.py` 受版本控制、可搜索，适合跨命令和设备复用。`data/pyload.py` 不走 import、不受版本控制，当前承载设备特定 Minecraft 配置、screen/REPL 辅助、人格短句、编码函数和 link 依赖；`###` 会继续向它追加代码。它适合现场注入，不适合作为新公共接口的默认归宿，并可能含机器配置或凭据，不能复制到文档。

当前 `loc` 在执行 `pyload.py` 前建立，link 分派时才刷新；冷启动后的首条直接 `.py` 可能看不到 `pyload.py` 新名称。这是现行缺陷。如何保留即时持久化并收束名字环境见[未实现提案](working/proposals/core-and-failure-domains.md#动态执行环境的收束方向)。

## Config、cache 与 storage 的所有权

`config.json` 是 ops 和昵称的持久权威；`cache.ops`、`cache.nicknames` 是启动时加载的运行时镜像。`.op` 修改镜像后调用 `ops_save()` 写回 config。运行中直接手改 config 不会自动刷新 cache，后续 `.op` 还可能用内存镜像覆盖磁盘修改。

绝大多数后来增加的配置和业务状态由 `s3.storage` 持有：命名空间对应目录，键对应 JSON 文件，模块取得内存可变对象，退出或显式 `storage.save()` 时整文件写回。运行中手改 storage JSON 不会自动载入，并可能在退出时被内存旧值覆盖。

选择文件而非数据库最初来自可理解性：JSON、文本和目录可以直接阅读、备份、编辑和修复；不需要数据库服务或迁移器。代价是缺少事务、并发写协调、局部更新和高效查询，但当前规模并不自动要求换数据库。

Config 只有在 ops、昵称和首次启动流程都迁移到 storage 或其它单一权威、且所有读写调用被删除后才能退役；在此之前不能同时维护两份可写管理员真相。热同步、校验和原子写方案见[Storage 提案](working/proposals/storage-and-history.md)。

## 应用日志与 chatlog

`main` 当前导入 `s3.__logging`，它配置根 logger、每日滚动的 `app.log` 和 7 天保留期；主路径同时仍大量 `print`。`s3.log` 定义另一套 logger，但主路径 import 已注释，没有发现当前生产调用，是重复的非主路径。

删除 `s3.log` 的条件是：确认全仓和设备级扩展没有调用它，并由唯一 logger 保留需要的等级与滚动行为。统一 logger、incident 和 link 报错节流仍是[未实现提案](working/proposals/errors-and-logging.md)。

`bot.chatlog` 是产品历史而非应用诊断：每条收发消息立即追加到按群/私聊和日期组织的文本文件，同时更新近期 cache。追加写不依赖正常退出，磁盘代价低；文本可用 `tail -f`、搜索器和编辑器观察，是终端复读全部聊天的上位替代。

Chatlog 有意不保存完整 OneBot JSON，只保留说话人、时间、消息 ID、正文和部分 notice 语义。它易读但有损，不能可靠恢复全部消息 dict、回复关系和未选字段。最近最多 256 条较完整事件另存 `data/cache_msgs`，但同一对象可能被后续 reply 规范化原地修改，也不是不可变原始包。双向恢复方案见[Storage 与历史提案](working/proposals/storage-and-history.md#chatlog-双向恢复)。

## 生命周期插件与一次性钩子

`.reboot` 和 `.shutdown` 不只退出：

- `.reboot` 等待“重启中”发送完成，把原消息写入 `data/reboot_greet.py`，以 233 请求 `run.py` 重启；插件下次加载发送“重启完成”并删除文件；
- `.shutdown` 发送“关闭中”，把一次性发送语句写入 `data/shutdown_greet.py` 后正常退出；下次加载发送“醒了”并删除文件；
- `cmds.load()` 会读取重启消息，在命令导入失败时把失败列表发回原聊天。

这是一个小型跨进程 continuation：只持久化目标消息和一次性动作，不保存整个运行环境。

## 当前权限模型

当前只有 master/op 与非 op。首位 op 是 master，`.op` 不允许直接移除 master；但 op 能使用 `.py`、shell 和文件能力，技术上可以绕过限制并控制 Bot/宿主机，因此 master 保护不是安全隔离。

权限不是集中式中间件：`cmds.run()` 直接调用插件，每个命令自行检查 `cache.ops`。新增高权限命令若忘记检查，默认向普通用户开放；`.mcf` 当前没有显式 op 检查。`.post` 允许普通用户修改当前窗口，在群聊中实际影响整个群共享路由。

整数级别和正则/函数例外属于[未实现权限提案](working/proposals/permissions.md)，不是当前行为。

## 尚未实现的设计

所有候选方案从[提案索引](working/proposals/README.md)进入。现行文档只保留链接，直到代码路径和验证完成后再把事实迁回本文。
