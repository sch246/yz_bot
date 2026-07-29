# 源码覆盖索引

> 状态：覆盖实现和根入口直接切换已于 2026-07-29 完成。旧来源只作迁移追溯，目标职责以 [Mods 模块手册](module-manual.md) 为准。

本页只在新系统设计完成后回答“当前版本控制内的能力是否已被覆盖、旧文件最终去哪里”。它不参与推出目标结构，也不重复定义目标模块职责。无法解释目的的旧代码应标为待核对或删除，不能因为本页追求逐文件覆盖就自动迁入新系统。

## 入口和根目录代码

| 当前来源 | 去向 |
|---|---|
| [`run.py`](../../../run.py) | 原位保留，改为监督根目录 `main.py` |
| `_code/main.py` | 信号/finally → 根 `main.py`；路由 → `mods.bot`；发送 → `mods.message`；其余手工导入删除 |
| `_code/funcs.py` | 按手册中的 [`funcs.py` 拆分索引](module-manual.md#funcspy-拆分索引) 分配，不保留 `mods.funcs` |
| `_code/chat.py` | 删除：未接入当前主路径的旧 LLM client |
| `_code/tool.py` | 当前 `s3.chat` 使用的 `Tool` 合入 `mods.llm` |
| `_code/vendor.py` | 删除：未接入当前主路径的旧供应商包装 |
| `_code/import_history.py` | 与 HippoRAG 历史导入工具一起删除 |
| `_code/test.py` | 删除：未接入运行路径的 HippoRAG 试验脚本，不建立测试替代物 |

## `bot/` 非命令文件

| 当前来源 | 去向 |
|---|---|
| `_code/bot/__init__.py` | 删除空包装 |
| `_code/bot/cache.py` | 当前消息/catches → `context`；近期消息 → `history`；资料缓存 → `identity`；ops → `op`；任意 globals 容器删除 |
| `_code/bot/chatlog.py` | `mods.chatlog` |
| `_code/bot/connect_with_http.py` | `mods.connect`，socket 创建移入 `on_load` |
| `_code/bot/connect_with_ws.py` | 删除：当前主路径不用旧 WebSocket connector |
| `_code/bot/cq.py` | `mods.cq`；与新 image cache 重复的下载实现只留一条路径 |
| `_code/bot/data.py` | `strip_re` 合入 `mods.link`，文件删除 |
| `_code/bot/msgs.py` | `mods.msgs` |
| `_code/bot/pages.py` | `mods.pages` |

`_code/bot/cmds/` 的 `__init__.py` 去 `mods.command`，`_bbx.py` 去 `mods.bbx`；其余每个命令文件已逐项列在[模块手册](module-manual.md#命令与具体功能)。其中 `ba_gacha.py` 和 `test.py` 删除。

## `s3/`

| 当前来源 | 去向 |
|---|---|
| `_code/s3/__init__.py` | `debug` → `log`；`Show` 在 `op` 内用普通字符串替代；确认保留的动态 helper 进入显式导出，不保留 `s3` 包装 |
| `_code/s3/__logging.py` | `mods.log` |
| `_code/s3/__schedule.py` | 删除：当前运行路径不用的旧 `sched` 实现 |
| `_code/s3/add_attr.py` | `mods.attrs`，作为显式动态工具保留 |
| `_code/s3/aes.py` | 合入保留的设备叶子模块 `mods.portfunc` |
| `_code/s3/cache_args.py` | 合入唯一消费者 `mods.later` |
| `_code/s3/chat.py` | `mods.llm`；文件末尾天气/手动 test 函数不迁移 |
| `_code/s3/command_manager.py` | 合入唯一稳定消费者 `mods.chat` |
| `_code/s3/config.py` | `mods.config` |
| `_code/s3/counter.py` | 小型计数逻辑放回 `file` 等唯一消费者，不建立模块 |
| `_code/s3/delay_func.py` | 发送节流合入 `mods.message`；不建立通用 delay mod |
| `_code/s3/file.py` | 合入 `mods.file` |
| `_code/s3/ident.py` | 随旧 WebSocket connector 删除 |
| `_code/s3/image_description_cache.py` | 合入 `mods.image` |
| `_code/s3/image_identity.py` | 合入 `mods.image` |
| `_code/s3/linux_screen.py` | `mods.screen`，当前设备脚本和 live link 仍使用 |
| `_code/s3/log.py` | 删除第二套 logger；不与 `mods.log` 并存 |
| `_code/s3/mc.py` | `mods.mc`，当前设备脚本和 live link 仍使用 |
| `_code/s3/mcrcon.py` | `mods.mcrcon`，随 MC 能力保留 |
| `_code/s3/params.py` | `mods.params`，作为显式动态工具保留 |
| `_code/s3/rag.py` | 删除：当前 `main` 已注释且 import 即创建外部资源 |
| `_code/s3/repl.py` | `mods.repl` |
| `_code/s3/rsa.py` | 合入保留的设备叶子模块 `mods.portfunc` |
| `_code/s3/scheduler.py` | `mods.scheduler`，start/atexit 改成生命周期钩子 |
| `_code/s3/src.py` | `mods.src`，作为 op 信任域动态工具保留 |
| `_code/s3/storage.py` | `mods.storage`，底部自动 load/start/atexit 改成生命周期钩子 |
| `_code/s3/str_tool.py` | `mods.text` |
| `_code/s3/thread.py` | `mods.thread` |
| `_code/s3/tool.py` | 删除与根 `tool.py` 重复的旧实现 |
| `_code/s3/url_to_base64.py` | 合入 `mods.image`，顶层建目录移入 `on_load` |
| `_code/s3/vision_input.py` | 合入 `mods.image` |

## 其它源码

| 当前来源 | 去向 |
|---|---|
| `_code/portfunc/` | 合成协议模块 `mods.portfunc`；Git 历史中的 client `call()` 及 live link 消费者由 `mods.server` 持有具体设备实例 |
| `_code/hipporag/` | 当前主路径未使用的引入源码；默认连同 `rag.py`、离线导入脚本一起删除，不逐个改造成 mod |
| [`scripts/migrate_image_cache.py`](../../../scripts/migrate_image_cache.py) | 保持独立一次性维护脚本，不被 `mods` 扫描；确认迁移已完成后再决定删除 |

## 覆盖审核结果

- 模块手册中的保留/删除选择已经固定；当前可加载命令默认保留，明确占位、重复或已知失效且无消费者的实现删除。
- 已在维护者授权下只读核对当前设备 `pyload.py` 和实时 link；所需模块类别进入目标手册，必要平铺名称进入 `py.py` 的显式 `DYNAMIC_EXPORTS`。
- 旧 `getcmd` 模块返回语义不与新 command registry 复用同名入口；动态查询统一为 `getmod`，已知 link 消费者改为直接模块引用。
- `funcs.py` 中的农历/小六壬进入独立 `lunar.py`；名字、@ 和 C++ 交互由 `identity/chat/cpp` 的已有机制承载，无消费者的 `msgtext`、`my_product`、`testprint` 不迁移。
- `shownotice` 是临时 raw notice 调试节点，明确删除且不升格为 capture。
- live link 中只出现、但仓库和持久 pyload 都未定义的名称标为运行期注入，不建立假兼容对象。
- HippoRAG 和旧 WebSocket/LLM/logger 路径没有当前主路径消费者，明确删除；MC、图片服务、语言工具和 `portfunc` 作为叶子能力保留，不进入核心依赖。

实现阶段仍要逐项把旧责任标成“新系统已承载”或“已删除”，但不再需要新的架构选择。“覆盖完整”表示每项旧能力都有明确去向，不表示每段旧代码都有新副本。
