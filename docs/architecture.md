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

路由不改写事件字典。`message` 从 NapCat 送来是什么，写进 chatlog、进 `history`、送到模型面前就还是什么；回复关系、去掉回复后的正文和 at 列表是它的三个投影，由 `mods.msgs` 的纯函数 `reply_cq()` / `body()` / `at_cq()` 现算。这不只是风格：`bot` 曾在写完日志之后就地剥掉开头的 reply CQ 并挂上 `reply` / `at_cq` 两个键，于是实时事件和从同一份文件重建出来的那条事件不一样——重建的带着 reply CQ、却没有那两个键，模型因此只在重启后看见字面的 `[CQ:reply,...]`。派生值不落在数据里，这个差别就无从产生。规则是：**解释**消息的地方（`^C`、`.` 命令、shell 前缀、link 条件、延续式回答、单 CQ 判定）读 `msgs.body()`，**存储和显示**消息的地方读原始 `message`。

当前事件与等待下一条输入由 `mods.context` 持有；近期 256 条窗口事件由 `mods.history` 持有；完整可读聊天历史由 `mods.chatlog` 追加写入。三者职责不同，不能相互替代。

`chatlog` 的行格式是协议，逐条写在[chatlog 记录格式](chatlog-format.md)里，改动即破坏性变更。记录形状是「记录头顶格、正文缩进 4 格」：`【头衔】名字(QQ号) 时:分:秒 | 消息号`，私聊同形但没有 `【头衔】`。正文存 OneBot 原始串，末尾空白按规则清除；`chatlog.display()` 是唯一的显示投影，终端回显和 `.search` 都经过它把正文 unescape 回可读形式，因此人看到的内容与切换前一致，而文件保留的是事实。`.search` 也按这个形状分组：命中任何一行都返回它所属的整条记录，附 `path:line` 定位头（通知没有正文，是一条单行记录），所以搜正文能看到是谁说的、搜人名能看到说了什么；结果是记录原文，不经过 `parse_log`。`format_message` 与 `parse_log` 是互逆的一对纯函数，`parse_log` 同时能读切换前的旧记录并用 `_derived`/`_missing` 标出哪些字段是推导值、哪些根本没写下来。切换时刻记在 `storage` 的 `chatlog/format`。历史记录只读，不重写；旧记录缺私聊发送者身份，这一缺口的确切大小和其余取舍见[统一消息模型](working/proposals/message-model.md)。

因为写入总是先落盘再进内存，文件是内存的超集：`history.getlog(msg)` 不带范围时仍是一次 dict 查找、返回活列表，给了 `since`/`until` 则转由 `chatlog.read_range` 只读文件并返回带来源标记的新列表。代价写在签名里而不是藏在名字后面，热路径因此不为罕见的范围查询付固定成本。

`history` 因此是纯内存，不再自己持久化：启动时由 `chatlog.on_load` 从文件重建近期窗口，只重建切换点之后的 v1 消息记录与戳一戳，并剔除已被撤回的消息——v0 正文是投影、v0 私聊没有发送者，放进内存都是静默的错误内容。戳一戳和撤回是 notice 里仅有的两个例外，因为它们各自的消费者只需要行尾那个 id，而那个 id 是写入侧自己写下去的；其余 notice 仍是不透明文本。重建不出东西时内存就是空的，随新消息长回来。

## 命令：函数就是入口

命令由无参数 `@command` 注册。命令名从模块名和函数名机械派生：

- `mods.bar.run` 对应 `.bar`；
- `mods.bar.foo` 对应 `.bar.foo`。

命令函数继续接收命令名后的原始 `body`。返回普通值会发送；返回 generator 会登记为当前用户、当前窗口的 continuation。Import 或 Load 失败模块留下的注册会被过滤，因此失败功能不会占住点命令入口。

命令依然处于 Bot 进程的宿主机信任域。权限不是统一中间件；高权限函数必须显式调用 `mods.op`。普通模块应直接 import 已知依赖，不通过命令注册表反查模块。

## `.py`、`pyload.py` 与动态名称

`mods.py` 在 `LATE` 阶段建立一份共享 `loc`：

1. 放入所有 Import 成功的模块及 `ctx`；
2. 合并版本控制内明确维护的 `DYNAMIC_EXPORTS`；
3. 在候选字典中执行 `data/pyload.py`；
4. pyload 全部成功后才替换正式环境，失败则只安装版本控制内环境。

稳定公共 helper 已升格为普通 mod，并在确有 live link 消费者时显式平铺导出。`data/pyload.py` 只保留设备值和现场试验代码，不再与正式 helper 形成第二份实现。模块名不可被 pyload 覆盖；覆盖显式平铺名会在启动终端列出。

动态代码只有在模块名本身运行期计算时才使用 `getmod(name)`。已知模块依赖直接 import；旧 `getcmd` 不再存在。旧根 `data.json` 已退役，动态环境中的 `data` 是 `storage.get("", "py_data")` 的直接引用。

## 统一 LLM 工具模块

`mods/tools/` 是工具和可按需载入说明的唯一目录，`mods.tools.ToolRegistry` 持有进程级 last-good 模块表。顶层 `foo.py` 和 `foo.md` 具有同一种模块语义：第一行是开局可见的模块描述，余下文本是激活后加入当前聊天 system 提示的内容；Markdown 模块没有函数，Python 模块通过 `__all__` 导出一组普通函数。Python 文件可以正常 import 第三方依赖、其它 `mods` 和同目录 `_helper.py`，导出函数仍由现有 `Tool` 校验，模型侧名称通常为 `foo__function`。

首次使用 registry 时，每个顶层模块独立尝试进入 last-good；单模块失败只记录 traceback。之后修改磁盘不会自动改变运行版本：`list_tools` 查看差异，`reload_tools` 才逐模块读取、执行、校验并原子替换 last-good，失败保留旧版；`load_tools` 不读磁盘，只把 last-good 模块的说明和函数激活到当前 `Chat`。因此进程级“已应用源码”和聊天级“已激活能力”是两层状态，没有 watcher、变化 hint 或兼容旁路。

`meta.py` 是唯一默认激活的必需模块，保存工具维护说明并导出 `exec_code`、`list_tools`、`reload_tools`、`load_tools` 四个无前缀恢复入口。`__init__.py` 只持有 registry、last-good 和 per-Chat binding 机制；它不再伪装成工具格式。`meta` 调用通过一次调用范围内的 `ContextVar` 取得当前 binding，多 Chat 和 `assign_tasks` 工作线程不会共享错误会话；磁盘删除 `meta.py` 的 reload 会失败并保留旧 last-good。

可用模块的第一行描述从一开始就在同一条 system 提示中；激活时原地更新这条既有消息，并修改当前 Chat 的工具映射。每个 LLM 子请求仍冻结一份工具快照，同一份快照同时决定请求 schema 和响应调用解析，所以加载或重载只从下一子请求起生效，不改写已发出的请求或同一响应中的其它调用。`mods/tools/disable/README.md` 只保存历史上被明确禁用的工具的决策记录，不再保存它们的实现。

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

`mods.llm/` 持有模型配置、client、流式响应与每请求工具快照；`mods.chat` 持有 QQ 窗口上下文、提示和聊天命令；`mods.tools` 持有统一 registry、当前 Chat 绑定以及按职责分类的真实工具实现，天气模块只投影现有 `mods.weather`；`mods.image/` 持有图片身份、缓存和视觉输入。图片与子任务工具只惰性调用 `mods.chat` 的既有 usage/cost 入口，不复制计费状态。`llm`、`tools` 和 `image` 采用文件夹 Module，是因为各自内部实现共同拥有明确状态；具体能力仍优先由普通函数表达。

终端打印是实际运维界面的一部分：收发消息、流式 LLM 内容、工具调用、图片捕获/缓存、link/capture 命中、模块失败和生命周期进度都应保留可观察输出。这些产出者各自选一条**流名**（`mods.log.stream(...)`，即 `yz.*` logger），终端订阅其中一组；流是「属于哪个子系统」这条轴，与 logger 级别表达的「是不是故障」互不替代。每条流有自己的 `log/<流>.log`；`app.log` 只记严重程度；chatlog 保存产品聊天历史，三者不是同一用途。落盘的记录由一个 filter 自动补上交互归属，调用点不传参。终端行为、订阅集与 `.log` 见[运行时](runtime.md)。

## 单实例、权限与退出

一个进程服务一个 Bot 账号。cache、storage、link 环境、命令表、scheduler 和 connector 都是进程级单例；多个独立 Bot 项目共存不改变本项目内部的单实例事实。

master/op 与普通用户仍是当前权限边界。op 能使用 `.py`、shell、文件、link 和宿主控制能力，技术上拥有 Bot 进程和宿主机控制权。整数权限体系仍只是[未实现提案](working/proposals/permissions.md)。

`.reboot` 以退出码 233 请求外层监督者重启；`.shutdown` 以 0 正常退出。第一次 Ctrl+C 由 `run.py` 转发，`main.py` 进入同一 `finally`，随后逆序停止 scheduler、listener、worker、子进程与 storage。重复 SIGINT 在保存期间被抑制；SIGKILL、断电和解释器 fatal error 不经过这条有序路径。

## 历史与未完成项

旧 `_code/main.py`、`funcs.py`、`bot/`、`s3/` 和旧命令扫描器已经退出正式代码树；其能力去向见[模块手册](working/proposals/module-manual.md)和[源码覆盖记录](working/proposals/source-coverage.md)。这些文档现在是迁移记录，不是第二套运行契约。

Storage 值校验与历史版本、统一 incident/节流、更细权限和多项目协调仍从[提案索引](working/proposals/README.md)进入。
