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
用户发送 `.hello 文档不匹配时` → 返回 `run.__doc__`（帮助文本）

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

1. `__init__.py` 在启动时扫描 `bot/cmds/` 下所有非 `_` 开头的 `.py` 文件
2. 每个文件通过 `importlib.import_module('bot.cmds.xxx')` 导入
3. 加载成功的命令存入 `modules` 字典
4. 加载失败的存入 `fails` 列表，启动时通过消息通知

### 消息路由

```python
# _code/main.py 的 recv() 中
if text.startswith('.') and cmds.is_cmd(text[1:]):
    _cmd_ret(cmds.run(*cmds.is_cmd(text[1:])))
```

`is_cmd(text[1:])` 按 `commands` 列表顺序匹配：匹配第一个前缀相同的命令，且命令名后不能紧跟非空白符。例如 `.py print(1)` → 匹配 `py`，body = ` print(1)`。

**命令优先级** > link 系统 > python 执行（`!`）。

---

## run() 函数详解

### 基础签名

```python
def run(body: str):
    '''帮助文本
格式:
.命令名 <参数说明>'''
```

- `body`：命令名后面的部分（已 strip 开头的空白），不包含 `.` 和命令名本身
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

### 参数解析

```python
from main import read_params

s, last = read_params(body)
# s = 第一个空格前的词
# last = 剩余部分

s, last = read_params(body, 2)
# s = 第一个词, last = 第二个词开始的部分（用于取多个参数）
```

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
- 用户发送 `^C` 可以取消交互（`catches` 中对应 key 被删除）

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
├── /                       # 空命名空间（默认）
│   ├── cave.json           # storage.get('', 'cave')
│   ├── links.json          # storage.get('', 'links')
│   └── cave_pool.json
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
- 非基本类型的键（如 `int` key）在 `save()` 时会被 `skipkeys=True` 跳过
- 不可 JSON 序列化的值会被 `default=lambda x: None` 转为 null

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
.link re 回应 while
: {称呼:柚子|YuZU}
: s='我在'
```

**py 类型**（Python 代码）：
```
cond: msg = cache.thismsg()\nis_msg(msg) and msg['message'] == 'ping'
action: sendmsg('pong')
```

cond 最后一行作为布尔表达式判断通过/失败，`#` 开头视为 None。

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

---

## pages 翻页展示

```python
from main import pages

def run(body: str):
    items = [f'第{i}条记录: 内容...' for i in range(100)]
    # 返回一个在 __send__ 中自动迭代的生成器
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
is_private_msg(msg)   # 是否私聊消息
is_notice(msg)        # 是否通知
is_recall(msg)        # 是否撤回通知
is_heartbeat(msg)     # 是否心跳
is_img(msg)           # 消息是否仅含一张图片
```

---

## 调试技巧

### 本地测试命令逻辑

可以在 `test.py` 中模拟：

```python
# 模拟消息上下文
from _code.main import cache
cache.thismsg({'user_id': 123456, 'message': '.hello world', 'group_id': None})

# 直接调用命令
from _code.bot.cmds import hello
print(hello.run('world'))
```

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
