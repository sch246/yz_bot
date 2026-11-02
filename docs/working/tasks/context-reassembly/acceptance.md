# 整份规格验收

本记录对照 [spec](spec.md) 检查上下文概览、分身交付与权限、同调用换段、构造/主行动接线和空解析修复。各票记录提交、认领与审查历史；票据 done 是整体验收的输入，不能单独证明交付。以下源码与行为证据由实施代理和主代理取得，T06 核对票据、关键接口与文档，不重复运行 Python 验证。

## 固定基线与集成范围

- 主工作树 `/root/bot`，任务前 main 为 `0225bb9a557a45750a6fddeec8a803fef294f8ed`。
- 隔离源码基线 `31371596ae873576341c01c72e27e6203ea809dd` 包含本机既存的 11 个 Python 文件改动，只用于隔离实施和验证，不能整体合入 main。本任务功能差异为 `3137159..ffbc185`。
- 代码隔离集成树 `ffbc185` 含 T01–T05 和主行动首段 focus 修正。主代理另把任务代码差异投影到 `0225bb9`，排除上述 11 份既存改动，得到临时 clean commit `9a4982ed0eaf3237329d35581bd1d3aa3dea5e80`；该提交只用于验证任务代码可独立应用，本地 main 的既存修改已保留为未提交改动，未混入本任务提交。
- 本地 main 已集成本任务代码与文档，提交只包含本次差异；生产进程已按下文记录完成切换。最终提交号由交付回复给出。

基线 `--check` 退出 0，154 个 Python 文件、88 个公开模块；首轮 `--smoke` 日志记录为 86/88（当时未保留命令退出码，后续各票及最终检查均保留了退出 0），六个 required core 加载成功，可选失败仅 `mcf`、`minecraft`。工作树最初没有 `.venv` 入口，首试被 run.py 拒绝；补上本机环境链接并使用 `--no-sync` 后取得基线，没有同步或改写共享依赖。

## 跨切片行为证据

临时脚本位于仓库外，使用合成输入、临时运行根、假模型和假 OneBot；没有使用生产聊天、真实模型或 QQ 接口。通用命令前缀为 `UV_PROJECT_ENVIRONMENT=/root/bot/.venv uv run --frozen --no-sync python`。下表中的脚本路径与运行次数取自各票实际交付记录，不能推导真实使用收益。

| 范围与出处 | 实际验证与结果 |
| --- | --- |
| [T01 空解析](tickets/T01-修复空聊天记录解析的画面越界.md) | `/tmp/yuzu-reassembly-l6cuhjly/screen-check.py`：旧版 12 次 IndexError，新版 12 次通过；14 组正常消息、通知和未识别行逐字段等价；10 组合成日档经真实 prepare/rescan 保留原文与身份，不新增召唤。空解析沿用 unparsed，不伪造发送者或消息号。 |
| [T02 分身交付与限制](tickets/T02-分身只交回最终答复并继承准备权限.md) | `/tmp/yuzu-reassembly-l6cuhjly/fork-validation/check.py`：最终答复选择 8 类、真实 run/schema/dispatch、load/reload 和嵌套 fork 过滤、三项并发有序、普通显式发言授权，以及取消/异常/工具结束的归档均通过。写笔记、exec、发言、assemble 与策略写入逃逸被阻断。 |
| [T03 概览](tickets/T03-提供首请求边界与用量的上下文概览.md) | `/tmp/yuzu-reassembly-l6cuhjly/outline-flow.py`：19 次假派发，main/fork/construct 与并行 current 不串线；first 冻结，原始 usage 与请求对应，cached 缺失/0/123 可区分，正文与 match 兼容，来源版本和所有请求累计正确，无完整请求正文重复持久化。`outline-boundaries.py` 覆盖实际派发钩子、供应商转换前后、空 schema、缺 usage、流关闭、派发前失败和旧归档缺口，均通过。 |
| [T04 同调用换段](tickets/T04-辅助运行在同一调用内重新组装上下文.md) | `/tmp/yuzu-reassembly-l6cuhjly/reframe-flow.py`：52 次假模型派发、25 段迭代，父调用最终返回一次，refs、usage、权限和段间关联正确。`reframe-boundaries.py`：15 次派发，覆盖坏引用原段继续、混合批次无副作用且不确认、取消/原绝对截止时间、迟到真实回执、未知不重放及 I/O/归档失败，均通过。 |
| [T05 构造与主行动](tickets/T05-接入构造和主行动并保留唤醒所有权.md) | `/tmp/t05-validation.py`：33 次假模型真实 schema/dispatch，构造两段/一次 assemble、按需概览、清工具后恢复、嵌套只读 fork、末段 replay、失败/超时/取消通过。真实 main prepare/confirm 覆盖混合批次不 ack、合法 reframe 确认、同窗插话/另窗 deferred、实际 read patches、history、最新及空草稿。真实 reader 正常/取消/异常/retry 异常均 touch=1、release=1，仅正常 next_loop=1，retry 前未确认 claim 已释放。focus 增量记录于 `/tmp/t05-validation-focus.txt`：首段等于 spec.focus，第二段仍为 main-next。 |

旧职责的接管与删除：fork 不再拼接流式 pieces 或工具前自语交付；current 不再借主 current_wake 定位辅助运行；run 迭代管理段切换与整调用累计；主行动 prepare 从当前 state 读取 view/history，替代初始 history/草稿闭包，并在每段取一次最新草稿。原有 wake 认领、读确认和最终 reader 清理仍各有一个权威。

T04 曾因“初始可选工具加载本来容错”的判断重开。主代理与原作者对照 `3137159` 和 `f70ce18` 后确认原路径本来严格，排除误报，未引入拟议补丁；原 `e91aeb6` 代码和验证仍有效。

## 检查与独立审查

每个 Python 切片实际运行 `run.py --check`，均为 154 文件/88 模块通过；T04、T05 的前后隔离 smoke 均为 86/88，可选失败只有 `mcf`、`minecraft`。最终主代理分别在 `/root/worktrees/yuzu-reassembly-integration` 与 `/root/worktrees/yuzu-reassembly-clean` 运行 `UV_PROJECT_ENVIRONMENT=/root/bot/.venv uv run --frozen --no-sync python run.py --check` 和同前缀的 `run.py --smoke`，两树均通过 `.venv` 符号链接复用本机环境，未同步依赖，结果如下：

| 源码范围 | `--check` | `--smoke` |
| --- | --- | --- |
| 隔离集成 `ffbc185` | 退出 0，154 文件/88 模块 | 退出 0，86/88，optional 仅 mcf、minecraft，与基线一致 |
| 任务代码独立投影 `9a4982e` | 退出 0，154 文件/88 模块 | 退出 0，86/88，optional 仅 mcf、minecraft，与基线一致 |

最终 smoke 日志分别为 `/tmp/yuzu-reassembly-l6cuhjly/final-integration-smoke.log` 与 `/tmp/yuzu-reassembly-l6cuhjly/final-clean-smoke.log`。smoke 证明本机隔离根下的核心闭环能加载并退出，不证明设备功能、真实模型响应或真实聊天体验。

独立 Standards 审查固定范围 `3137159..f5939d7`、`f5939d7..bb9f1fc` 均无发现；主代理核对首段 focus 的两行增量。独立 Spec 审查完整 `3137159..ffbc185`（含 focus 增量）无发现。T06 文档检查和最终 main 集成审查由下文记录。

T06 实际运行 `git diff --check`，退出 0；`python3 /tmp/t06-links.py` 校验本次涉及的 README、architecture、interaction-model、llm、提案索引、来源提案与本验收记录，共 7 份文档、139 个相对 Markdown 链接（含锚点），0 个问题。校验没有扩展到其它未修改文档。主代理另核对 runtime 的逐段封存措辞、概览调用示例和沉淀时机；最终检查覆盖 17 份本次文档、165 个相对链接及锚点，0 个问题。git diff --check 通过。

## 交付范围与剩余观察

隔离代码已接通 fork、construct、main 的 reframe，覆盖同调用连续换段、失败/取消/期限、权限继承、请求概览和主行动所有权；没有遗留一条仅定义而未接线的新路径。正式行为移入 [architecture](../../../architecture.md)、[interaction-model](../../../interaction-model.md) 和 [llm](../../../llm.md)，[来源提案](../../proposals/context-reassembly.md)保留调查、原子/串线视角与未采纳的 return_result 取舍。

本轮没有改变构造默认开场材料，没有新增任务树、自动换段阈值、全局模型预算、自动副作用重放或生产笔记整理。跨文件归档不是事务，崩溃后不承诺自动续跑；超时返回时 worker 仍可能在退出并等待归档，已完成回执仍按现有规则保留。

尚未测量生产中的总费用、端到端延迟、补读与遗漏，或回应是否保留关系和表达来路。评估须计入构造、行动和全部分身，不能由末段变短、缓存前缀比例或合成通过数推出省费、选材改善。T06 已由主代理审查，整份规格的工程交付完成；生产切换记录如下。

## 本地集成与启动

- 2026-11-02 15:34:39 +0800：先向外层 run.py 发 SIGINT，旧 run/main 全部退出，5701 停止监听；没有使用更强信号。
- 仅应用 `3137159..d7b3cc9` 的功能差异，代码与经过隔离验证的集成树逐文件相同。提交的代码另与排除本地改动的 clean 验证树相同；原有 11 份追踪文件修改仍未提交。
- 按原方式在 tmux `0:0.0` 执行 `uv run python run.py -a`，只有一个 Bot 实例。新终端从“启动中”到“连接完成”“加载完成”，自动重启模式保留，5701 由新进程监听。
- 启动后观察约两分钟，按启动前 app.log 字节位置核对增量：ERROR 与 traceback 均为 0，包含 chat、construct、settle、agent、agents 的启动检查。只记录状态和计数，未复制聊天或日志正文。
- 没有执行数据迁移、手工修改生产数据、发送测试消息或调用真实模型作验收；没有 push。新功能何时被实际选择，以及选材、总账和体验的变化，仍按前节观察。
