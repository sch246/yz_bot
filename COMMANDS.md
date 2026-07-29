# Bot 命令与 Module 开发指南

本文说明当前 `mods` 架构的实现方式。用户可见入口顺序以[交互模型](docs/interaction-model.md)为准，生命周期和失败边界以[运行架构](docs/architecture.md)为准；现有命令的精确帮助仍以 `.help` 和函数 docstring 为准。

## 最小命令

在 `mods/` 创建普通模块：

```python
# mods/hello.py
"""问候命令。"""

from mods.command import command


@command
def run(body: str):
    """格式：.hello [名字]"""
    name = body.strip() or "世界"
    return f"你好，{name}！"
```

重启后：

- `mods.hello.run` 注册为 `.hello`；
- 若同模块还有被装饰的 `foo(body)`，它注册为 `.hello.foo`；
- `@command` 不接收命令名或其它参数。

命令函数接收命令名后的原始 `body`，包括分隔空白。返回非空普通值时框架发送它；返回 `None`/空字符串时静默；返回 generator 时进入延续式交互；返回 `SendFuture` 或兼容 future 时由统一结果处理器接管。

## Module 形态

一个公开 Module 可以是：

```text
mods/example.py
```

也可以是：

```text
mods/example/
├── __init__.py       # 唯一公开 Module 与生命周期入口
├── schema.py         # package 内部实现，不被 loader 单独扫描
└── helpers.py
```

同名文件和文件夹禁止并存。只有内部实现共同拥有明确状态或生命周期时才使用文件夹；普通叶子功能保持单文件。以下划线开头的文件不参与公开扫描。

稳定依赖直接 import：

```python
from mods import context, message, storage
from mods.command import command, grouponly, params
```

不要 `from main import ...`，不要建立第二个命令目录，也不要通过 command registry 查找模块。模块名确实只能在运行期确定的动态代码可用 `getmod(name)`。

## 生命周期

Module 顶层只做定义、内部 import 和装饰器注册。需要运行状态时声明：

```python
from mods import FEATURE

PHASE = FEATURE
LOAD_AFTER = ("storage",)


def on_load(ctx):
    ...


def on_exit():
    ...
```

阶段固定为 `INFRA → FEATURE → LATE`，默认 `FEATURE`。同阶段前后关系只在 `on_load()` 确实消费另一模块已初始化状态，或退出清理顺序需要约束时声明。

Import 失败模块不会进入 `ctx`；Load 失败模块仍可从 `ctx` 被诊断，但不会进入 `available`，它注册的 command/capture 不会暴露。成功模块的 `on_exit()` 按 Load 逆序执行。因此端口、storage、线程、scheduler job、client 和子进程应分别在 Load/Exit 中建立和收束。

## 当前消息、参数和权限

当前事件来自：

```python
from mods import context

event = context.current()
```

它是原始 OneBot `dict`，可能包含 `user_id`、`group_id`、`message` 等字段。使用 `mods.msgs` 的函数式谓词判断事件类型。

简单命令可直接处理 `body`。旧命令常见的 `(msg, params..., line, extra_lines)` 形状可用 `@params` 适配；群聊限定可用 `@grouponly`。装饰器顺序应让 `@command` 位于最外层，注册最终包装后的函数：

```python
from mods.command import command, grouponly, params


@command
@grouponly
@params
def run(event, target, line, extra_lines):
    ...
```

权限不是全局中间件。宿主机、动态执行、文件、进程、配置和管理员能力必须显式调用：

```python
from mods import context, op

if not op.require_op(context.current()):
    return None
```

遗漏检查就表示普通用户可调用；不要依赖命令名或模块阶段获得权限。

## yield 延续式交互

```python
from mods import msgs
from mods.command import command


@command
def run(body: str):
    reply = yield "请输入名字"
    if not msgs.is_msg(reply):
        return "操作终止"
    return f"你好，{reply['message'].strip()}"
```

等待按“群或私聊窗口 + 用户”隔离。同一交互线的下一条消息会直接送进 generator，即使它看起来是另一个点命令。`^C`/`^c` 会通过 `mods.context` 取消 generator 和 `.py input()` 的等待。

耗时普通函数可使用 `mods.thread.to_thread`，其 future 会由命令结果处理器继续发送或报告异常。线程内若依赖当前消息，包装器或调用者必须保留正确的 context；不要把进程级 globals 当作线程局部消息。

## Storage

```python
from mods import storage

settings = storage.get("hello", "settings")
items = storage.get("hello", "items", list)
namespace = storage.get_namespace("hello")
```

返回值是进程内共享引用；原地修改会被后台同步观察，正常退出会强制保存。需要立即落盘时调用 `storage.save()`。需要明确用磁盘覆盖内存时调用 `storage.load(namespace, name)`。

不要在新功能中直接增加根 `data.json` 或另一套 JSON manager。设备地址、密钥路径等私有值放在忽略的 `data/device/` 或环境变量；普通业务状态放 storage；临时缓存由具体 Module 自己拥有。

## `.py` 动态环境

`.py` 和 link 共用 `mods.py.loc`。环境包含成功 Import 的 Module、`ctx`、版本控制内的 `DYNAMIC_EXPORTS`，以及成功执行的 `data/pyload.py` 内容。

新增稳定 helper 的顺序是：

1. 放入职责明确的普通 mod；
2. 普通消费者直接 import；
3. 只有 live link/pyload 确实需要旧平铺名字时，才在 `_EXPORT_SPECS` 显式加入映射。

不要把稳定逻辑重新塞进 `pyload.py`。pyload 是设备现场输入；它执行失败时整批不安装，不能覆盖 Module 名。旧 `getcmd` 已删除，动态模块查询只保留 `getmod`。

`.py` 多行内容的前几行用 `exec`，最后一行用 `eval` 并发送非 `None` 结果。最后一行以普通 `#` 开头时静默；以 `###` 开头时把完整片段追加到 `data/pyload.py`。

## 线性 link

持久 link 位于 `data/storage/links.json`，是按优先级排列的四字段数组：

```json
{
  "name": "hello",
  "type": "re",
  "cond": "^你好$",
  "action": "'你好呀'"
}
```

分派顺序只有一条：从前到后检查，首个命中节点执行 action 后结束。不存在 `succ`、`fail`、`while` 或反向边。

- `py` cond：前几行 `exec`、末行 `eval`，truthy 表示命中；action 整体 `exec`，需自行调用 `sendmsg()` 等。
- `re` cond：使用 `mods.text.stc_get` 的结构化模板做前缀匹配；action 先替换捕获值，再执行前几行并对末行 `eval`，非 `None` 结果自动发送。
- cond 报错记录后按未命中继续；首个命中节点的 action 报错会结束本次反应。

`.link re|py` 创建或修改，`.link get/del/move/list/captures/catch/save/load` 管理。`.link catch` 只禁止 action，仍会真实执行 cond，所以不是无副作用 dry-run。

普通精确文本回复可直接使用 `mods.link.set_literal()`，不必生成复杂 Python 模板。

## 进程内 capture

源码功能需要参与自然语言捕获、但不应写入用户可编辑 links 时，使用独立注册器：

```python
from mods.capture import capture


@capture
def fallback(event):
    ...
    return True  # 已消费
```

默认 capture 在全部持久节点之后。要保留已经证明的旧优先级：

```python
@capture(before="some-link")
def image_reply(event):
    ...
```

声明在 Import 时收集，所属 Module Load 成功后才激活。capture 不写 `links.json`；异常记录后继续后面的线性节点。

## 分页、消息与文件

- `mods.pages.display(items, page_size)` 返回 generator 式翻页交互。
- `mods.message.sendmsg(value)` 发到当前窗口；`send()` 指定目标；发送完成后会查询消息并写 chatlog。
- `mods.cq` 处理 CQ 解析、转义和媒体引用。
- `mods.file` 是完整宿主机文件能力，不是沙箱；业务模块只复用所需函数。
- `mods.repl`、`mods.screen` 和语言 Module 管理长驻或临时子进程；按需启动并在 `on_exit()` 清理。

## 检查与调试

纯语法和公开 Module 冲突检查：

```bash
uv run --frozen python run.py --check
```

它不会 import `mods`，不会绑定 `5701`。不要为语法检查直接 import `main.py` 或 `mods`；Import `mods` 就会执行完整 Import/Load，读取真实状态并绑定真实 listener。

运行时观察重点包括：模块 Import/Load 汇总、收到/发送消息、link/capture 命中、LLM 流输出与工具调用、图片捕获/缓存、scheduler/storage 退出。终端输出是运维界面的一部分，不要把现有打印当作无用噪声删除。

新增 Module 的最小核对：

1. 名称与职责唯一，没有同名文件/package；
2. 稳定依赖使用普通 import；
3. 顶层没有运行副作用；
4. `@command` 无参数且名称派生正确；
5. 高权限能力显式检查 op；
6. 状态所有权和退出清理明确；
7. `run.py --check` 通过；
8. 需要真实消息验证时，由维护者在唯一生产实例上低风险观察，不向 `5701` 注入合成事件。
