# Mods 两阶段加载

> 状态：已实现并由仓库根入口启用；本文保留精确设计规则

目标只有一个：把 Bot 还原成一组平等的普通 Python 模块，不再由 `main.py` 手工中转名称。

## 目录和依赖

`mods/` 下每个非下划线 `name.py` 或含 `__init__.py` 的 `name/` 都是一个公开 Module，名称就是模块名；同名两种形态禁止并存。Module 之间直接使用普通 import：

```python
from mods import storage
from mods.command import command
```

稳定依赖只用 import 表达。`ctx` 只交给确实需要全部模块引用的代码。

## Import

`mods/__init__.py` 按名称排序盲扫所有非下划线 `.py` 与同级 package，依次调用 `importlib.import_module()`。loader 不递归扫描 package 内文件；生命周期元数据和钩子由 package 的 `__init__.py` 提供，内部命令的所属 Module 取 `mods.<name>` 的顶层名称。Module 顶层只定义函数、对象、内部 import 和注册项，不绑定端口、不读写运行文件、不启动线程或 scheduler。

某个模块 import 失败时打印异常并继续；失败模块不进入 `ctx`。command/capture 的 Import 期声明记录函数所属模块，失败模块已经产生的部分声明会被过滤并清除。capture 声明只有在所属模块成功 Load 后才激活。

## Load

Import 全部结束后建立：

```python
ctx = {
    "storage": <module mods.storage>,
    "chat": <module mods.chat>,
    "py": <module mods.py>,
}
```

`ctx` 表示 Import 成功、定义已经可见的模块，不等同于 Load 成功列表。某模块的 `on_load()` 失败后仍可留在 `ctx` 供 `.py` 观察和现场诊断，但命令等普通外部入口不会把它当作可用功能。

每个模块可以声明加载阶段和少量前后关系：

```python
from mods import INFRA

PHASE = INFRA
LOAD_AFTER = ("storage",)

def on_load(ctx):
    ...
```

只有三个严格阶段：

```text
INFRA → FEATURE → LATE
```

`PHASE` 默认是 `FEATURE`。阶段只决定生命周期顺序，不改变模块身份；没有 `on_load()` 的模块不执行钩子，并在排到自身时直接标记为可用。

同一阶段内根据 `LOAD_BEFORE` / `LOAD_AFTER` 排序，没有约束时按模块名排序。普通 import 不自动表示生命周期顺序；只有 `on_load()` 必须消费另一个模块初始化出的值，或逆序退出时必须先于另一个模块清理，才声明前后关系。

loader 在执行任何 `on_load()` 前先算出完整顺序：未知阶段、与阶段冲突的前后关系或循环关系直接报错；引用没有成功 import 的模块只警告并忽略。跨阶段且方向一致的关系允许，但不改变阶段顺序。

端口绑定、storage 读取、后台线程、scheduler job、缓存恢复等运行期初始化都放进 `on_load()`。某个 `on_load()` 失败时打印异常并继续，不回滚。没有 `on_load()` 的模块在 Load 排到自身时标记为可用；声明了 `on_load()` 的模块只有在钩子成功返回后才标记为可用。命令等外部入口只暴露可用模块，避免失败功能继续占住入口。

Load 汇总后检查 `REQUIRED_MODULES = {"bot", "command", "connect", "context", "message", "storage"}`。其中任一模块没有成功 Import/Load，整体启动失败；其它功能继续使用上述隔离规则。这个普通集合只表达最小运行闭环，不赋予模块不同的接口或基类。

## Exit

模块可以定义 `on_exit()`。`mods` 记录所有已标记为可用的模块；完整进程退出时按可用顺序的逆序调用其中存在的 `on_exit()`。单个退出钩子失败时打印异常并继续，确保后面的模块仍有保存和关闭机会。

多数没有 `on_load()` 的纯定义模块也不需要退出钩子；若它运行期间可能创建子进程等资源，仍可只定义 `on_exit()`。Import 或 Load 失败的模块不调用 `on_exit()`。这套机制只处理完整进程退出，不提供模块热重载。

## `.py` 和 link

`.py` 与 link 只是 `LATE` 阶段的普通模块：

```python
from mods import LATE

ctx = None
PHASE = LATE

def on_load(loaded):
    global ctx
    ctx = loaded
```

`link` 若需要 `.py` 已经完成初始化，只需声明：

```python
LOAD_AFTER = ("capture", "py")
```

通用反应不 import `link`。业务模块从轻量 `capture.py` 使用 `@capture`；loader 在该模块成功 Load 后激活声明。默认 capture 排在持久节点之后；若旧行为证明它必须早于某个持久节点，用 `@capture(before="link-name")` 把它合并到同一条线性顺序，目标缺失时降到末尾。capture 不进入 `links.json`，也不参与持久编辑；执行异常按未消费处理，记录后继续后续线性节点。

它们最后取得完整 `ctx`，把模块引用放进共享执行环境，供后续 `exec()` 使用。普通模块仍然直接 import，不通过这个全局字典取稳定依赖。

`pyload.py` 逐步只保留设备上的试验代码；执行失败就不安装本次内容，不影响其它模块。

## 入口

```text
run.py
  → main.py
      → import mods       # Import + Load
      → mods.bot.run()    # 消息循环
      → mods.exit()       # finally 中逆序退出
```

`run.py` 继续只负责进程监督和完整重启。`main.py` 不再是名称出口或依赖表。

## 最小核对

实现 loader 前只需一次性确认：三个阶段严格排序；同阶段前后关系和循环检查正确；失败模块不暴露命令；`.py` 能从 `ctx` 看到所有成功 import 的模块；一次 link 式调用能消费某个模块在 `on_load()` 中产生的运行期值；退出钩子按成功加载逆序执行。不为具体功能建立长期测试。
