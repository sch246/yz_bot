# 本地功能改动交接：适配新的聊天核心

> 2026-11-01。维护者要求把阻断部署的本地改动保存到独立分支，附说明，交给 Claude 合并。本分支保留原实现，尚未适配新的经历日志和无状态中心。本轮未迁移生产、未停止或启动 Bot。

> **已合并（2026-11-01，Claude）**：按下文的功能拆分叠加到 main，取舍和与原实现不同的地方见文末“合并结果”。现行行为以 [LLM 文档](../llm.md)、[交互模型](../interaction-model.md)和 [CLI agent 窗口](../cli-agents.md)为准。

## 接手入口

- 分支：`codex/local-changes-handoff-20261101`。
- 原基线：`aa7c597ecb6fdbcd9dcdcb4684a8a441aa38dd1a`。
- 本地改动快照：`1299204`，31 个文件，新增 1,535 行、删除 145 行。包括 24 个已跟踪文件的改动，以及 7 个新源码／文档文件；逐文件核对过，与 stash 前一致。
- 已核对的目标：`53561baaf1bca145ab4703e9855946beac7ac387`。接手时以最新 main 为底座，至少包含此提交。
- 本机另留一份带用途说明的 stash，未 pop 或 drop。接手只需要远端分支，不依赖这个本机 stash。

先读目标版本的 `AGENTS.md`、运行文档、[一次一个目的的设计与实施记录](https://github.com/sch246/yz_bot/blob/53561ba/docs/working/proposals/one-purpose-stateless-core.md)，再沿下面的功能拆分移植。本地新增内容以 `aa7c597..1299204` 为准；不要把旧基线与新核心之间的全部差异都当作待保留的本地功能。

```sh
git fetch origin main codex/local-changes-handoff-20261101
git show --stat 1299204
git diff aa7c597 1299204 -- mods docs README.md
```

目标 main 已先后替换了经历存储、工具 binding 和中心执行方式。本分支没有合入这些更新，整体覆盖目标文件会恢复旧接口。应在最新 main 上按功能取增量，保留目标的 journal、construct、main、settle 运行路径。

## 这些改动做了什么

| 功能 | 本地文件 | 已确认的内容与整合边界 |
| --- | --- | --- |
| CLI agent 窗口 | [agents 包](../../mods/agents/__init__.py)、[使用说明](../cli-agents.md)，以及 chat/windows、screen、awaker、chatlog、meta | 注册 DSH、Codex、AGY 窗口；后台运行，保存真实会话号，结果回写并叫醒主体；配套自定义窗口历史、wait、Markdown 标题保留和单任务发送。目标仍有 `chat.kind/push` 与 `chatlog.append_record`，但配套行为尚未包含，需作为一组移植。 |
| 独立 DSH 工具 | [tools/dsh.py](../../mods/tools/dsh.py) | 提供同步 `ask` 和后台 `ask_bg`，是 CLI 窗口之外的入口；后台依赖既有 `_tasks.submit`，目标签名仍吻合。CLI 可执行文件、默认路径和真实会话协议未运行验证。 |
| B 站工具 | [tools/bili.py](../../mods/tools/bili.py) | 视频链接及短链解析、动态翻页与去重、浏览历史、分享标记，字幕改用不强制登录的凭据读取。目标未包含等价功能，相关底层接口仍在；可以单独移植，尚未验证真实接口。 |
| GIF、图片、视频 | [image/vision.py](../../mods/image/vision.py)、[llm](../../mods/llm/__init__.py)、[附图辅助](../../mods/tools/_vision.py)、[视觉工具](../../mods/tools/vision.py) | GIF 原图／首帧选择、分帧网格和时间戳、视频抽帧拼图；独立描述开关、旧图降级、主动附图在 off 时仍可用；DeepSeek Files API 默认与缓存字段调整。图片底层功能可保留，旧图边界需适配新运行方式。 |
| 温度与表达 | [chat 设置](../../mods/chat/__init__.py)、[子命令](../../mods/chat/subcommands.py)、旧 agent、llm、[写作规则](../../mods/tools/less_ai_tone.md) | 温度设置及不思考时的默认值、英文节奏提示、三条写作规则。参数与提示须接到目标 `chat/run.py` 的会话创建和新提示入口，纯 Markdown 规则无聊天 API 依赖。 |
| 群聊等待 | [meta](../../mods/tools/meta.py)、旧 agent | wait 支持附带理由、明确等待任何人；群聊未指定 who 时默认绑定本轮对话人，减少无关消息消耗等待。目标没有旧的 `session.interlocutors`，需重新接对话人来源。 |
| 年鉴 | [segment](../../mods/chat/segment.py)、子命令 | 归档定位和重建索引。目标已在年鉴写 `[ctx]`，保留目标实现即可；本地重建器扫描旧 `archive/*.json`，不能直接用于新 `views/<ctx>.json` 的 `stats/items` 结构。 |
| 文档 | [上下文讨论](proposals/context-prediction-and-permeability.md)、LLM／交互模型／目录 | 上下文讨论与 `53561ba` 完全一致。其它本地文档描述新增功能，应按最终整合后的行为补进目标文档，保留目标对新核心的说明。 |

CLI 新包需要同时保留 `_process.py`、`agy.py`、`codex.py`、`dsh.py` 与 `__init__.py`；初始化时会导入这些兄弟模块。仅漏掉整个包不会使其它新增源码因直接 import 而崩溃，但这些窗口不会注册。独立的 `mods/tools/dsh.py` 则是另一条工具入口。

## 已核实的接口断点

### 工具执行记录不能丢

目标 [chat/run.py](https://github.com/sch246/yz_bot/blob/53561ba/mods/chat/run.py#L174) 注册 `on_tool_start` 与 `on_tool_result`，在每次派发前、每个结果完成后立即写经历日志；批完成回调只标记完成。本地旧 `mods/llm/__init__.py` 没有这两个回调的调用路径。

若用本地 llm 整文件覆盖目标，回调属性赋值可能仍然成功，但逐个工具结果不会记录。应把温度、图片等功能叠加到目标 llm，保留目标派发、逐结果记账、取消与写盘失败处理。

### binding 与唤醒状态已经换形

本地 meta 使用 `current_binding().session`，目标 [current_binding](https://github.com/sch246/yz_bot/blob/53561ba/mods/tools/__init__.py#L44) 返回字典，运行状态由 `binding["run"]` 等字段提供。新增 wait 的对话人读取也沿用旧 session，因此不能只解决文本冲突就视为兼容。旧 dismiss、tidy、compress 路径已由新核心删除，整合时保持删除。

CLI／B 站等自定义窗口的等待还有一处接线差异：本地 `chat.push` 主动认领 wait；目标 `attention.on_record` 的该路径要求事件带 `message`，而目标 `push` 生成的事件只有 `text`。目标的新路径尚未等价覆盖本地行为，需沿实际事件检查，不要只移植目标字符串解析。

### 图片新旧边界要重新接

本地旧 agent 把前次唤醒留下的 message 对象交给 `session.image_history`，llm 通过 `id(message)` 判断旧图。目标 [run 的组装](https://github.com/sch246/yz_bot/blob/53561ba/mods/chat/run.py#L152) 会从视图重新 render messages，且一次唤醒可先构造、再跑多个独立行动。直接移植底层分支不能实现原来的跨唤醒行为。

整合时需要说清构造、行动续轮、显式召回旧图各自怎样处理，并核对实际请求。GIF 导出、描述缓存版本及 Files API 缓存字段也应一并处理，不能只复制单个工具函数。

### CLI 不是只新增一个包

目标 chatlog 的窗口枚举／路径反查仍以 QQ 为主；screen 仍会过滤以 `#` 开头的文本；awaker 的目标解析也没有本地新增的 CLI 窗口支持。要连同包、发送是否拆气泡、历史读取、画面过滤和等待一起核验。

## 文本合并检查

以 `aa7c597` 为共同基线，在仓库外分别用本地完整文件和 `53561ba` 文件执行 `git merge-file --diff3 -p`。没有修改运行目录。24 个追踪文件的结果是：14 个文本合并干净、9 个冲突、1 个完全相同。新增 7 个文件在目标没有同名文件。

9 个冲突文件：

```text
docs/llm.md
docs/working/proposals/README.md
mods/chat/__init__.py
mods/chat/agent.py
mods/chat/segment.py
mods/chat/subcommands.py
mods/llm/__init__.py
mods/tools/_vision.py
mods/tools/meta.py
```

这只是文本合并结果。上面的图片身份、工具绑定和自定义窗口问题，还涉及跨文件调用关系。目标 main 若继续前进，应重新检查受影响部分。

## 已做与接手后要验证的事

本轮已完成：

- stash 前后、独立工作树与快照逐文件一致性检查；未擅自修补原实现。
- 静态检查：`UV_PROJECT_ENVIRONMENT=/root/bot/.venv uv run --frozen --no-sync python run.py --check`，退出码 0，152 个 Python 文件、89 个公开模块。借用现有环境但未同步依赖。
- 差异格式和交接说明相对链接检查。
- 提交范围仅为源码和文档。没有收入运行数据、配置、日志、缓存或 `.scratch/`。

未做运行兼容验收、smoke、模型调用、CLI 实际任务、视频抽帧或 B 站接口调用。静态检查通过不表示此分支可直接部署到新数据格式。

Claude 整合后应按目标仓库要求执行 `run.py --check` 和隔离 `--smoke`，比较模块数及可选失败；用仓库外合成材料核对上述接口断点，不启动第二个真实实例。生产迁移与部署仍沿维护者给出的独立流程执行：此前在未提交改动检查处停止，本轮没有执行停机、运行数据备份、迁移或两处生产文本修改。

## 合并结果（2026-11-01，Claude）

main 没动过的 11 个文件原样取用：agents 包、`tools/dsh.py`、`tools/bili.py`、`tools/vision.py`、`tools/less_ai_tone.md`、`chatlog.py`、`chat/view.py`、`image/` 两个文件、`llm/models.py`、`llm/types.py`。其余按功能接到新核心上：

- **工具执行记录**：`llm` 用三方合并，保留 main 的逐结果记账回调，温度、描述开关和图片处理叠加上去。
- **图片新旧边界**：一次运行从视图重建请求，`id(message)` 认不出旧图，改由 `run` 给借来的行打 `_old_image`：构造带进来的旧材料和 fork 继承的引用算旧图，只给地址和已有描述；这次运行自己写下的行（行动的画面、附图结果）照档位展开。构造运行看到的画面仍是原文。
- **退回路径的附图**：不收 tool 图的视觉模型，附图仍按 main 的做法只活下一次请求，不进经历日志；本地加的 `_tool_image` 标记保留，所以 `off` 下也能主动看图。
- **画面里的图片**：合并时发现 cf8f0d4 以后画面不再经过 `view.outside`，聊天图片只剩 CQ 字样，语音档位和 QQ 表情注释也没了。行动画面现在经 `view.screen_content` 重新套用这三项。
- **wait 默认等谁**：没有旧的 `session.interlocutors`。改为在 `agent._adopt` 记下叫醒她的人（召唤的作者，排除 Bot 自己），`send` 在群里不写 `who` 时读它。本地实现还会被之后任意一条未读的作者覆盖；合并时没有照搬，因为那会把路人当成对话人。
- **push 认领 wait**：没有放在 `chat.push` 里，而是在 `attention.on_record` 让 `post_type == "push"` 的记录也认领。
- **awaker 目标**：`dsh:main` 能挂 `on`/`wait`。写错的目标（如 `group:1`）记成不认识的行，不会让整份规则解析失败；模块没加载的种类照旧能读。
- **节奏刹车**：只加在行动运行的提示末尾，构造、沉淀和反思不加。
- **温度**：除构造外的运行按 `#temperature` 设置；构造有自己的 effort，不跟着升温。`#temp` 别名原先没进命令表，合并时补上了。
- **年鉴**：新核心的年鉴行本来就带 `[ctx]`。迁移来的 views/ 里旧段 kind 不全，整份重建会冲掉手写行，所以 `#years rebuild` 改成只补缺行、不删不改。`#years` 读 `notes.years()`，因为 years 不是普通笔记。

验证：`run.py --check` 154 个文件、88 个模块；`--smoke` 86/88，可选失败仍是 `mcf`、`minecraft`。原有 38 项合成检查全部通过。另有 23 项一次性检查，用假模型和假的 OpenAI 客户端核对实际请求，全部通过：

- 新旧图、GIF 原图与网格、`off` 下的附图、温度参数，以及标记不外泄；
- 群聊 wait 默认等叫醒她的人、`who="any"`、`#brake off`；
- 假 `dsh` 可执行文件的整轮往返、真实会话号回写、wait 认领与两条唤醒原因；
- awaker 解析、子命令与年鉴补缺。

没有调用真实 CLI、B 站接口或 ffmpeg 抽帧。

