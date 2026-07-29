# 代码组织现状

> 状态：2026-07-26 只读调查摘要；当前行为仍以[运行架构](../architecture.md)为准，目标见 [Mods 两阶段加载](proposals/mods-loading.md)。

## 当前装配

`_code/main.py` 手工导入 `s3`、`bot`、LLM、storage 等模块，再执行 `from funcs import *`，最后由 `cmds.load()` 导入命令。许多模块反向从尚未初始化完成的 `main` 取得名称，因此 import 顺序同时承担依赖和初始化顺序。

`_code/bot/cmds/` 当前以文件名和 `run(body)` 提供低样板命令，这个简单形态值得保留；问题是命令依赖通过 `main` 隐式获得，而不是文件插件本身。

## 混合文件

`_code/funcs.py` 同时包含：

- 当前消息、发送和用户/群 helper；
- LLM 上下文和图片旧实现；
- 天气、petpet、2048、小六壬等具体功能；
- `.py`/link 使用的动态词汇和固定内容。

它不能原样改名为 `mods.funcs`，否则只是把旧公共出口搬到新目录。迁移时按实际消费者把内容放入普通模块；无消费者或不再需要的功能直接删除。

`_code/bot/cache.py` 也混合近期消息、当前线程消息、用户/群资料、ops、昵称和任意 globals 容器。迁移时只需让这些状态回到使用它们的普通模块，不需要设计统一状态系统。

## `.py`、link 与 `pyload.py`

当前 `.py` 先通过 `from main import *` 建立 `loc`，再把 `data/pyload.py` 执行到模块 globals；link 调用前又刷新一次 `loc`。名称来自多个位置，冷启动时还存在同步差异。

目标中 `.py` 和 link 都是 `PHASE = LATE` 的普通模块，在 `on_load(ctx)` 中取得完整模块字典并共享一个执行环境；link 可以声明在 py 后初始化。`pyload.py` 中稳定能力逐步迁入 `mods/`，最终只留下设备试验。

## 必须移出 import 的行为

- HTTP listener 的 bind/listen；
- storage 的文件读取、watcher 和 worker；
- scheduler 启动与 job 注册；
- cache、`.py` 的持久状态读取；
- later/todo 的后台任务恢复；
- reboot/shutdown 的启动问候；
- LLM client、图片缓存目录和其它外部资源初始化。

这些行为进入对应模块的 `on_load(ctx)`。顶层只保留定义和内存注册。

storage 保存、scheduler 等待、listener/子进程关闭进入对应模块的 `on_exit()`；退出钩子只对已标记为可用的模块逆序调用，`on_load()` 失败的模块不进入这条序列。

## 历史与可删除代码

旧 WebSocket、重复 logger、旧 LLM/Tool、未接入主路径的 RAG/HippoRAG、占位命令和重复定义都不是新架构必须保留的内容。删除前只需确认仓库、实时 link 和设备试验没有仍需的入口；不为可能删除的功能建立新抽象。

目标职责与当前来源的逐项映射见[模块手册](proposals/module-manual.md)和[源码覆盖索引](proposals/source-coverage.md)。
