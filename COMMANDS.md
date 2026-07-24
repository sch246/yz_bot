# Bot 命令开发指南

## 目录

- [快速开始：添加一个命令](#快速开始添加一个命令)
- [命令系统架构](#命令系统架构)
- [run() 函数详解](#run-函数详解)
- [yield 交互模式](#yield-交互模式)
- [storage 持久化存储](#storage-持久化存储)
- [link 系统](#link-系统)
- [pages 翻页展示](#pages-翻页展示)
- [多线程与信号](#多线程与信号)
- [常用模块速查](#常用模块速查)
- [调试技巧](#调试技巧)

---

## 快速开始：添加一个命令

### 最小示例

在 `_code/bot/cmds/` 下创建一个 `.py` 文件（文件名 = 命令名）：

```python
# _code/bot/cmds/hello.py
'''向世界问好'''

from main import sendmsg

def run(body: str):
    '''打招呼
格式:
.hello [<name:str>]'''
    name = body.strip() or '世界'
    return f'你好，{name}！'
```

用户发送 `.hello Alice` → Bot 回复 `你好，Alice！`
用户发送 `.hello` → Bot 回复 `你好，世界！`

> **重要**：文件名就是命令名。`hello.py` → `.hello` 触发。

### 返回值规则

| 返回值类型 | 行为 |
|-----------|------|
| `str` / 非 `None` | 作为消息发送给用户 |
| `None` / `''` | 不发送任何消息 |
| `Generator`（包含 yield） | 进入多步交互模式（见下文） |

---

## 命令系统架构

### 文件结构

```
_code/bot/cmds/
├── __init__.py     # 命令注册、加载、路由
├── hello.py        # → .hello
├── chat.py         # → .chat
├── link.py         # → .link
├── py.py           # → .py（Python 执行环境）
├── cave.py         # → .cave（回声洞）
└── ...
```

### 加载流程

1. `__init__.py` 导入时扫描 `bot/cmds/` 下所有非 `_` 开头的 `.py` 文件；顺序来自未排序的 `os.listdir()`
2. 每个文件通过 `importlib.import_module('bot.cmds.xxx')` 导入
3. 加载成功的命令存入 `modules` 字典
4. 加载失败的存入 `fails` 列表；有 reboot 来源消息时回传，否则只在终端打印

这套 `cmds` 就是项目的插件系统。插件只需要提供 `run(body)`，并可通过 `from main import ...` 获得几乎全部 Bot 能力。收益是添加命令的样板极少；代价是依赖没有自动解析，`main.py` 必须精确维护名称出现和插件加载的先后顺序。移动 import 或让命令提前加载都可能破坏启动，不应当作纯格式整理。

### 消息路由

```python
# _code/main.py 的 recv() 中
if text.startswith('.') and cmds.is_cmd(text[1:]):
    _cmd_ret(cmds.run(*cmds.is_cmd(text[1:])))
```

`is_cmd(text[1:])` 按 `commands` 列表顺序匹配：匹配第一个前缀相同的命令，且命令名后不能紧跟非空白符。例如 `.py print(1)` → 匹配 `py`，body = ` print(1)`。未知的 `.xxx` 不会被命令层消费，仍可能进入 link。

候选名 `commands` 和成功导入的 `modules` 不是同一集合：失败候选不会自动移除，后续调用可能 KeyError；`main.recv()` 又把 `modules['py']` 当作路由必需项，因此 `.py` 加载失败会影响所有事件处理。

完整入口顺序是用户可见协议，以[交互模型](docs/interaction-model.md#入口有优先级)为唯一说明；不要在插件指南中维护第二份顺序表。插件作者只需特别注意：成功匹配的文件命令优先于 link，但同一交互线里已经开始的 yield / `.py input()` 会先取得下一条消息；未知点命令仍可能落入 link。改变这些关系不是内部重排。

---

## run() 函数详解

### 基础签名

```python
def run(body: str):
    '''帮助文本
格式:
.命令名 <参数说明>'''
```

- `body`：命令名后面的原始剩余部分，不包含 `.` 和命令名本身；若有分隔空白，该空白会保留
- 返回 `str` 表示发送消息，返回 `None` 表示静默

### 权限检查

```python
def run(body: str):
    msg = cache.thismsg()
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, r'\.xxx'):
            return '权限不足(一定消息内将不再提醒)'
        return
    # ... 正常逻辑
```

- `cache.ops`：管理员 QQ 列表
- `cache.any_same(msg, pattern)`：防止权限提示刷屏，同一人在一定消息内不再提醒

命令加载器没有集中权限中间件，必须由每个插件自己检查。遗漏检查就会默认向普通用户开放；尤其是 `.py`、shell、文件、进程和宿主服务能力，不能依赖“命令看起来像管理员命令”获得保护。

### 参数解析

```python
from main import read_params

s, last = read_params(body)
# s = 第一个空格前的词
# last = 剩余部分

first, second, rest = read_params(body, 2)
# count=2 会返回两个词和剩余部分，共 3 个值
```

因为命令路由保留命令名后的原始内容，`body` 要么为空，要么通常从分隔空白开始；`read_params()` 会校验这一点。需要把引号包围的内容视作一个参数时使用 `read_str=True`。读取完的最后一个值始终是尚未消费的剩余文本。

### 使用 pages 翻页

```python
from main import pages

def _list():
    lst = [f'条目{i}' for i in range(100)]
    return pages.display(lst, 10)  # 每页10条
```

### 群聊限定

```python
from main import cache

def run(body: str):
    if not cache.thismsg().get('group_id'):
        return '此命令仅群内可用!'
```

`__init__.py` 提供了 `@grouponly` 装饰器也可用。

---

## yield 交互模式

当 `run()` 中包含 `yield` 时，Python 将其视为生成器，框架会进入多步交互：

```python
def run(body: str):
    reply = yield '请输入名字'
    if not is_msg(reply):
        return '非消息，操作终止'
    name = reply['message'].strip()

    reply2 = yield f'你好 {name}，请输入年龄'
    if not is_msg(reply2):
        return '非消息，操作终止'
    age = reply2['message'].strip()

    return f'{name}，{age}岁'
```

### yield 的行为

- `yield 'xxx'` → 发送 `'xxx'`，等待用户的下一条消息
- 用户的下一条消息通过 `yield` 的返回值获取（`reply` 是完整的消息字典）
- `is_msg(reply)` 检查是否是有效消息（不是通知、心跳等）
- 阻塞按 `(群号或私聊, 用户)` 隔离；同一用户在同一位置发送的点命令也会被当作 reply
- 用户发送 `^C` 或 `^c` 会删除 `catches` 中对应 key，原 generator 不再继续；当前实现中裸 `^` 也会命中
- 对 `.py input()` 使用的 Queue，这只会解除消息拦截，已经阻塞的 `Queue.get()` 线程不会收到取消信号，仍然等待

### 框架如何处理 yield

```python
# _code/main.py
def _iter_ret(gen):
    if getgeneratorstate(gen) == GEN_CREATED:
        _set_catches(gen)  # 注册到 catches
        return next(gen)   # 执行到第一个 yield，返回 yield 的值作为消息发送
    else:
        return gen.send(cache.thismsg())  # 把用户回复送入生成器
```

---

## storage 持久化存储

### 基础用法

```python
from main import storage

# 获取（不存在则自动创建，默认值为 dict()）
d = storage.get('namespace', 'key')        # 默认 dict
lst = storage.get('namespace', 'key', list)  # 默认 list
s = storage.get('namespace', 'key', str)     # 默认 str

# 直接操作命名空间
ns = storage.get_namespace('namespace')
ns['cave'] = some_dict  # 原地修改
```

### 存储结构

```
data/storage/              # 根目录
├── cave.json               # storage.get('', 'cave')：空命名空间直接位于根
├── links.json              # storage.get('', 'links')
├── cave_pool.json
├── namespace_a/
│   └── data.json           # storage.get('namespace_a', 'data')
└── llm_system/
    └── description_cache.json
```

### 保存时机

- `atexit.register(save)` — 正常退出时自动保存
- `exit(233)` — reboot 时触发 atexit → save
- `storage.save()` — 手动触发（如 `.link load` 之后）
- **Ctrl+C 安全性**：经过 `run.py` 修复后，现在 Ctrl+C 会转发 SIGINT 给子进程优雅退出

### 注意事项

- `storage.get()` 返回的是**引用**，原地修改会自动反映到存储
- `storage.load(namespace, name)` 从单个 JSON 文件原地覆盖对应内存 dict/list；它只在管理员显式调用时执行
- 非字符串基本键（如 `int`）会被 JSON 转成字符串；tuple 等 JSON 不支持的键会被 `skipkeys=True` 跳过
- 不可 JSON 序列化的值会被 `default=lambda x: None` 转为 null
- storage 在启动时载入内存、退出时整文件保存；运行期间直接手改对应 JSON 不会自动 reload，必须再显式调用 `storage.load(namespace, name)`，否则可能在退出时被内存旧值覆盖
- `reload(storage)` 保留当前 storage 对象且不自动全量载入；模块更新后仍由管理员显式调用目标 `load`

### 当前消息与近期记录

- `cache.thismsg()` 取得当前线程正在处理的原始事件 dict；传入 dict 时会设置它。
- `cache.get_last()` 取得最近记录的事件。
- `cache.getlog(msg)` 取得同群或同私聊的近期消息，**最新一条位于索引 0**。
- 每个聊天范围默认最多保留 256 条；这是运行时上下文和退出恢复缓存，不是完整历史检索库。
- `cache.get_one()` / `same_times()` / `any_same()` 只在这段近期窗口中查找，不应当作长期聊天记录查询。

---

## link 系统

### 概述

link 系统是一套可编程的消息匹配→响应链。每个 link 有 `cond`（条件）和 `action`（动作）。当消息进入时，从 `links[0]` 开始沿 fail/succ 链遍历。

### link 结构

```python
{
    'name': 'link名称',
    'type': 're',          # 're' 或 'py'
    'cond': '匹配条件',     # re: 正则模式, py: Python表达式
    'action': '执行动作',   # re: Python代码(可用{:name}), py: Python代码
    'succ': ['link名', ...], # cond通过后执行的link列表
    'fail': ['link名', ...], # cond失败后执行的link列表
    'while': {
        'succ': ['link名', ...],  # 反向引用：哪些link成功后触发我
        'fail': ['link名', ...],  # 反向引用：哪些link失败后触发我
    }
}
```

### 两种类型

**re 类型**（正则匹配）：
```
cond: "{name:pattern}" → 命名捕获组，匹配文本消息
action: "{:name}" → 替换为捕获的值
```

示例：
```
.link re 回应
{称呼:柚子|YuZU}$
===
'我在'
```

**py 类型**（Python 代码）：
```
cond: msg = cache.thismsg()\nis_msg(msg) and msg['message'] == 'ping'
action: sendmsg('pong')
```

cond 最后一行作为布尔表达式判断通过/失败，`#` 开头视为 None。

两种类型的 action 契约不同：

| 类型 | cond | action |
|------|------|--------|
| `py` | 前几行 `exec`，末行 `eval` 判真 | 整段 `exec`；不会自动发送末行表达式 |
| `re` | 模板展开后用 `re.match` 前缀匹配 | 替换 `{:name}`，前几行 `exec`、末行 `eval`；非 `None` 自动发送 |

`{name:type}` 会尝试在共享环境中求值 `type`：字符串作为正则，可迭代值组成候选，失败则按字面正则；`{name}` 根据首字母大小写选择非空白或跨空白匹配，`{:type}` 只插入模式，重复命名会变成反向引用。捕获值用于 action 文本替换，不进入 `.py` locals。

### 链结构

```
新link(默认) → fail → 旧link1 → fail → 旧link2 → fail → ...
```

- 新建默认 link（无 while）插入到 `links[0]`，fail 指向原来的 `links[0]`
- 自定义 link（带 while）追加到末尾，通过后继关系接入链
- 执行总是从 `links[0]` 开始

### 核心 API

| 函数 | 说明 |
|------|------|
| `get_link(name)` | 按名称查找 link |
| `del_link(name)` | 删除 link 并清理所有引用 |
| `set_link(name, dict)` | 创建或覆盖 link |
| `connect_link(a, b, 'succ'/'fail')` | 建立双向连接 |
| `disconnect_link(a, b, 'succ'/'fail')` | 断开双向连接 |
| `_set2(name, type, cond, action, whiles)` | 程序化创建/编辑 link |
| `formats_link(link, mode)` | 格式化显示 |

### 配置导入/导出

```
.link save [<path>]    # 保存到文件（默认 data/links_save.json）
.link load [<path>]    # 从文件加载
```

- 加载前验证每条 link 的结构完整性
- 验证失败保留当前配置
- 保存时若当前 links 与启动时有差异，要求确认

`.link catch` 会抑制 action，但仍真实执行每个 cond。`py cond` 可以包含任意 Python 副作用，因此 catch 不是纯 dry-run；cond 应遵守“不 send/recv、不改状态、不主动触发 action”的约定。

当前异常路径需要特别注意：cond 报错会把 traceback 发到聊天、返回 falsy，并沿 `fail` 继续；action 报错同样会回传 traceback，但节点仍可能继续 `succ`。后者是现行缺陷。聊天提醒节流和全局 link 报错开关尚未实现，候选设计见 [docs/working/proposals/errors-and-logging.md](docs/working/proposals/errors-and-logging.md)。

---

## cave 回声洞系统

### 概述

cave（回声洞）是一个社区留言板系统。用户可以向其中添加消息，或随机抽取历史消息查看。

每条消息包含：发送者昵称、QQ号、来源群、时间戳、消息文本。

### 数据结构

```python
{
    'msgs': {
        '0': {'sender': '...', 'qq': 123, 'time': '...', 'text': '...'},
        '1': {...},
    },
    'pool': ['0', '2', '5', ...],  # 随机池
}
```

- `msgs` 是 `{序号: 消息内容}` 字典，序号为字符串形式的整数
- `pool` 是随机抽取池，每次随机抽取 `pool` 中的一个序号，抽走后有 2/3 概率从池中移除

### 子命令

```
.cave              # 随机获取一条消息
.cave <id:int>     # 获取指定序号的消息
.cave add          # 添加消息（yield交互输入内容）
.cave addn <n>     # 将接下来n条消息合并为一条
.cave del [<id>]   # 删除消息（默认上一条）
.cave search <kw>  # 搜索关键词
.cave save [path]  # 导出到文件
.cave load [path]  # 从文件导入
```

### 配置导入/导出

```
.cave save [<path>]    # 保存到文件（默认 data/cave_save.json）
.cave load [<path>]    # 从文件加载
```

- 导出时若当前数据与启动时不同，会要求确认是否覆盖
- 导入前逐条验证消息结构（必需字段：sender、qq、time、text）
- 验证失败则保留当前数据并返回错误列表
- 加载成功后立即调用 `storage.save()` 持久化

### load 后的快照同步

加载成功后会更新 `_cave_startup_snapshot`，确保后续 load 操作不会错误地检测为"与启动时不同"。

---

## pages 翻页展示

```python
from main import pages

def run(body: str):
    items = [f'第{i}条记录: 内容...' for i in range(100)]
    # 返回一个由 _cmd_ret() 自动推进的生成器
    return pages.display(items, page_size=10, init_page=1)
```

### 在 py 环境中使用

```python
# .py
prints(content, page_size=10, init_page=1)
```

`prints` 内部使用 `yield` 等待用户翻页指令。

---

## 多线程与信号

### to_thread 装饰器

```python
from main import to_thread

@to_thread           # 默认返回 SimpleFuture
def heavy_task():
    time.sleep(5)
    return '完成'

future = heavy_task()  # 立即返回，不阻塞
result = future.result()  # 阻塞等待结果
```

### call_delay 装饰器

`call_delay(delay_secs, max_size)` 把普通函数包装成串行队列并返回 `SimpleFuture`。`delay_secs` 可以是数值或根据调用参数计算的函数；正数在调用前等待，负数在调用后等待。当前 `main.send()` 正是这样统一限制 OneBot 发送频率，命令本身不需要各写一套 sleep/queue：

```python
@call_delay(
    delay_secs=lambda *args, **kwargs: random.uniform(-0.3, -0.6),
    max_size=20,
)
def send(...):
    ...
```

普通回复可以忽略 Future；生命周期提示等必须确认完成的调用使用 `.result()`。需要注意：队列满、执行异常和超时都会进入 Future，忽略它也会忽略失败；当前超时只结束等待，不能真正停止底层工作线程。

### ctrlc_decorator

```python
from main import ctrlc_decorator

@ctrlc_decorator(lambda: print('清理中...'))
def blocking_recv():
    return socket.recv(1024)
```

Ctrl+C 时会执行回调函数然后 `exit(0)`。

---

## 常用模块速查

| 模块 | 导入方式 | 常用功能 |
|------|---------|---------|
| `cache` | `from main import cache` | `thismsg()`, `ops`, `nicknames`, `getlog()` |
| `sendmsg` | `from main import sendmsg` | 向当前上下文发送消息 |
| `send` | `from main import send` | 向指定 user/group 发送（需 user_id 或 group_id） |
| `cq` | `from main import cq` | CQ码编解码、图片下载、头像获取 |
| `msgs` | `from main import *` | `is_msg()`, `is_group_msg()`, `is_notice()` 等消息判断 |
| `file` | `from main import file` | `json_read()`, `json_write()`, `ensure_file()` |
| `str_tool` | `from main import str_tool` | `read_params()`, `stc_get()`, `stc_set()`, `addtab()` |
| `pages` | `from main import pages` | `display()` 翻页展示 |
| `storage` | `from main import storage` | `get()`, `save()`, `get_namespace()` |
| `connect` | `from main import connect` | `call_api()` 调用 OneBot API |
| `llm_cilent` | `from main import llm_cilent` | LLM 调用客户端 |
| `chatlog` | `from main import chatlog` | `write()` 写入聊天记录 |
| `getname` | `from main import getname` | 根据 user_id 获取用户昵称 |
| `getran` | `from main import getran` | 从列表中随机取一个元素 |
| `counter` | `from main import counter` | 计数器 |
| `scheduler` | `from main import scheduler` | 定时任务 |

### 消息判断（`msgs` 模块）

```python
is_msg(msg)           # 是否普通消息
is_group_msg(msg)     # 是否群消息
# 私聊通常判断为 is_msg(msg) and not is_group_msg(msg)
is_notice(msg)        # 是否通知
is_recall(msg)        # 是否撤回通知
is_heartbeat(msg)     # 是否心跳
is_img(msg)           # 消息是否仅含一张图片
```

---

## 调试技巧

### 本地测试命令逻辑

不要在临时脚本中直接 `import _code.main`：当前 import 会绑定 HTTP 监听端口、启动 scheduler、加载真实 storage，并注册退出保存器，不是隔离测试。

现有代码可以组合出私聊交互式集成调试：向 `5701` 注入虚拟私聊事件，再让该窗口执行 `.post <假 OneBot API 端口>`，后续 `send_msg`/`get_msg` 会被路由到假端而不发给 QQ。群映射当前会被 `user_id=None` 的默认路由覆盖，尚不能可靠截获 `main.send()`。详细步骤和副作用边界见 [`docs/runtime.md`](docs/runtime.md#组合式交互测试)。

这仍不是无副作用单元测试：入站消息会进入真实日志、storage、link action 和计划任务。

### 查看 link 链

```
.link list        # 列出所有 link 及链条
.link get <name>  # 查看单个 link 的 cond/action
.link catch       # 输入文本，查看会触发哪些 link
```

### 重启

```
.reboot     # op 可用，正常重启（触发 atexit 保存）
```

`.reboot` 会把发起消息保存为一次性钩子，重启后由命令插件向原聊天发送“重启完成”并删除钩子。`.shutdown` 同样留下只执行一次的下次启动问候。两者不仅是退出码封装，也是跨进程恢复交互的一部分。

### 日志

- 聊天记录：`chatlog/` 目录（按群/私聊分文件夹）
- 应用日志：`app.log`
- storage 数据：`data/storage/` 目录

---

## 添加命令 checklist

1. 在 `_code/bot/cmds/` 下创建 `<name>.py`
2. 文件顶层写 docstring（模块说明）
3. 实现 `def run(body: str):`
4. `run()` 内第一行写 docstring（帮助文本），格式含 `.命令名`
5. 处理权限（如需）：检查 `cache.ops`
6. 使用 `read_params(body)` 解析参数
7. 返回 `str` 发送消息，`None` 静默
8. 需要多步交互时使用 `yield`
9. 需要持久化时使用 `storage`
10. 需要翻页时使用 `pages.display()`
