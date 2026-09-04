# LLM 聊天、上下文与工具调用

本文描述当前源码中的 LLM 主路径。运行时供应商、模型、价格和提示词来自 storage，本页不复制设备上的真实配置、聊天内容、账号或密钥。工具模块以 `mods.tools` 的进程内 last-good registry 为权威，当前任务能调用哪些函数则由该 `Chat` 的模块绑定决定；模型是否真正收到工具，还取决于 `llm_system/config` 中对应模型的 `function_calling` 标记。

## 入口与状态范围

主要入口是 link 的 `chatting` 节点，而不是 `.chat` 本身。`cond()` 接受以下输入：

- @ Bot；
- 以“柚子，”开头的文本；
- 戳一戳 Bot；
- `#...` 控制命令。

群聊只有群号位于 `chat_groups` 时才进入这条路径；私聊没有这层群白名单。普通 `#` 命令先由本地 `CommandManager` 解析，返回的 callable 直接执行而不请求模型；`#poke` 和不能解析成控制命令的普通触发才会进入聊天。`.chat` 则建立一次单句请求，`.chat4` 是指定模型的兼容入口。

群内其它成员之间的戳一戳不会单独触发 LLM，但会作为群聊事件进入下一次聊天的近期上下文。因此“能被模型看见”与“立即触发模型”是两个不同边界。

每次请求都会新建一个 `Chat` 对象，但共享进程级 `LLMClient`、供应商客户端和 storage。会话连续性不是由长驻 `Chat` 实例保存，而是每次从当前聊天窗口的近期消息重新构建。因此：

- 群聊上下文由全群共同贡献；
- 私聊上下文属于该私聊；
- `provider/model` 形式的模型选择、prompt 和 image 开关由 `getchatstorage()` 按群或私聊保存；provider 由模型选择导出，不单独保存；
- 工具执行时读取的 `cache.thismsg()` 仍指向触发本轮聊天的事件。

## 上下文如何组装

`get_msgs()` 从当前窗口最近消息向前读取，最多取 `max_msg` 条、累计不超过 `max_token`；源码默认分别为 200 和 4000，但 storage 配置可以覆盖。

构建时会：

1. 跳过以 `#` 开头的本地控制消息和无关 notice。`#` 是一条跨模块约定：LLM 失败信息（`llm.Chat.chat`）、`.py` 与 link action 的 traceback、`#` 子命令的输出都以它开头，`chat.get_msgs` 据此把它们排除，使调试输出不回流进模型——它们占 token，还会让模型看到自己的错误堆栈。过滤对所有发送者一视同仁，Bot 自己发的与用户发的 `#help` 一样不进上下文。改任何一个生产端的前缀都会让那类输出开始回流，且不会报错；
2. 遇到“聊天开始”或“聊天结束”就停止继续回看；
3. 用 `msg2chat()` 把普通 OneBot dict 转成模型消息；群聊用户消息附带用户、名字、时间和消息 ID 元数据，私聊省略群内身份字段；
4. 群聊中所有戳一戳、私聊中只有戳 Bot 的事件，会复用 chatlog 的姓名/群名片格式生成 `role=user` 的本地事件文本；
5. 普通消息和戳一戳事件共同受 token 预算限制，超限时截断更早的内容。

`init_chat()` 最终按以下顺序建立请求消息：当前窗口选择的提示词、内置 base 提示、统一工具模块提示、当前群/私聊位置说明、聊天历史。工具模块提示从开局就列出每个 last-good 模块的名称和第一行描述；模块被 `load_tools` 激活后，其余内容原地加入同一条既有 system 消息，不会在 assistant tool call 与 tool result 之间插入新消息。提示词可以是全局 `settings`、`prompts` 中命名设定，或当前窗口直接保存的消息列表。

图片读取是当前窗口的三档设置，使用 `#image <mode>` 修改，使用不带参数的 `#image` 查询当前档位。名称与数字别名分别为 `off/0`、`lazy/1`、`eager/2`；历史布尔值兼容为 `False → off`、`True → lazy`。`off` 会把历史消息中的图片 part 降级为 `[图片(URI)]` 文本：既避免把 `image_url` 发给纯文本模型，又让模型可以按需调用 `recognize_image`。`lazy` 只在 LLM 聊天实际触发时处理本轮上下文中的图片。`eager` 在图片消息到达时立即启动后台下载：当前聊天模型能直接读图时只缓存原图，纯文本模型才会同时预生成文字描述。同一来源若恰好同时被 eager 和聊天请求，描述生成会等待同一个进行中任务，避免重复计费。纯文本模型的识别结果统一压成一个 `[图片(URI)识别结果：描述]` 文本 part，明确 URL 与描述属于同一张图片；视觉模型收到的实际图片 part 前会保留一段“下方图片的原始链接”文本。图片加载失败或没有可用视觉模型时也使用同一个带 URI 的单段格式。

自动描述使用固定 prompt，要求直接概括可见内容、转录重要文字、标明不确定项，并禁止“如果你愿意我还可以……”一类元话术。自动聊天图片处理、eager 预取、`recognize_image` 和参考图生图共用图片解析入口：`http://`、`https://` 和本机绝对 `file://` URI 都解析为 SHA-256 内容身份；网络图片按摘要缓存在 `data/tmp_files`，本地文件只计算摘要而不复制。所有入口都执行图片格式和 20 MiB 上限校验。需要把图片交给视觉模型时，再统一由 `image_uri_to_data_uri` 编码。`recognize_image` 支持自定义识别 prompt，不设置输出 token 上限，也不读写自动描述缓存，避免长文本识别被截断以及不同识别任务互相串用答案。

视觉模型实际收到图片前会检查已经编码的图片尺寸。若图片高度严格大于宽度的 4 倍，原图会被仅在内存中切成从上到下排列的多个 PNG 片段：每片从 3 倍图宽的整数倍高度开始，先覆盖 3 倍图宽；若后续仍超过 1 倍图宽则向后延伸四分之一图宽作为重叠，否则直接延伸到末尾，因此片段最高为 4 倍图宽。这些片段替代原图，并附带 `【自动图片切割】` 提示，让模型按一张连续图片理解并避免重复转录。原生视觉聊天和为纯文本模型生成图片描述共用这项处理；分片不写入文件或缓存，自动描述仍按原始图片的内容摘要缓存最终结果。生图参考图不经过这项识别预处理。

新生成的描述缓存以图片 SHA-256 为键，值包含 `description`、`cached_at` 和自动描述 prompt 版本。迁移脚本也能保留 QQ URL 自带内容摘要的历史描述：`multimedia` 使用 `sha1:<摘要>`，`gchat` 使用 `md5:<摘要>`；这些迁移键只用于命中描述，不伪装成可解析原图的 alias。只有视觉调用成功才写入新描述；lazy 和 eager 对字节相同的图片直接复用文字，即使来源 URL 不同。进行中识别也以“描述缓存对象、内容摘要、prompt 版本”合并。

摘要是内部身份，不是模型或工具参数。原始 URI 始终保留在 `[图片(URI)]`、`[图片(URI)识别结果：描述]` 或视觉图片前的“原始链接”文本中，模型调用 `recognize_image`、`create_image_from_references` 等工具时继续传这个 URI；解析入口再用 URI alias 透明定位本地 SHA-256 文件。因此更换 `rkey` 不会改变同一 QQ 图片的来源身份，已有本地 alias 时也不必用旧链接重新下载。

`llm_system/image_uri_aliases` 保存来源 URI 到内容摘要的限时映射。QQ `multimedia.nt.qq.com.cn/download` 来源键只使用 `appid + fileid`，不把临时 `rkey` 当作图片身份；其它网络来源使用规范化完整 URL 的摘要；本地文件来源键还包含路径、大小和修改时间。alias、内容文件和描述分别按最后使用时间清理，互不作为永久引用。网络原图以 SHA-256 文件名保存在 `data/tmp_files`，命中时刷新文件时间；URI 解析活动会让内容文件和 alias 至多每 24 小时清理一次，自动描述处理活动会让描述至多每天清理一次，三者都以超过 15 天未使用为过期条件。Cave 使用的 `data/images` 永久图片不参与清理。

旧 URL-MD5 文件和 URL 键描述不由运行时兼容。升级前应停止 Bot，先运行 `python3 scripts/migrate_image_cache.py` 查看聚合报告，再按报告处理无法迁移项并使用 `--apply` 切换；脚本不访问网络，也不打印来源 URL 或描述正文，并在 `data/migrations/` 建立迁移备份。控制台在每次请求前打印完成图片转换后的最终消息，因此文本描述、原始 URI 和视觉模型实际收到的 Data URI part 都可核对；Data URI 只在日志中保留前 80 个字符并标明省略长度，发送给模型的数据不截断。

## 供应商与模型能力

`LLMClient` 从 `llm_system/config` 读取：

- providers、base URL 和 API key；配置值可以引用环境变量名；
- 每个 provider 的 models；
- `provider/model` 形式的默认模型和视觉模型；
- vision、function_calling、输入/输出价格等模型属性。

当前窗口可以用 `#model` 和 `#models` 查看当前选择及其模型列表。`#use_model <provider>/<model>` 会保存一个完整模型选择，并且只以参数中第一个 `/` 为分隔，所以模型名本身可以继续包含 `/`；不带参数的 `#use_model` 重置该选择。命令会拒绝配置中不存在的 provider/model 组合。源码内的默认配置只用于首次创建空配置，不能代表当前设备正在使用的服务。

运行中人工修改 `data/storage/llm_system/config.json` 后，只要对应内存仍等于 storage baseline，storage 自身的文件 watcher/轮询会把合法 JSON 原地载入内存字典；内存同时被改过时则拒绝覆盖。管理员仍可用 `storage.load('llm_system', 'config')` 明确强制选择磁盘版本。无论自动还是显式载入，都不会自动重建已经派生出的 provider 客户端；可以随后重启完成原子切换，或者对相关 `LLMClient` 实例显式调用 `reload_clients()`。

## 普通函数就是工具定义

工具不是单独的插件 class 层级。`Chat.add_tool(func)` 用一个很薄的 `Tool` 适配器读取普通函数：

- 函数名成为工具名；
- docstring 主段成为描述；
- `@param`/`Args:` 后的内容成为参数说明；
- Python 签名中没有默认值的参数进入 `required`；
- `str`、`int`、`float`、`bool`、容器和联合类型标注转换成 JSON schema；
- 参数说明中的 `enum: [...]` 转成枚举。

这延续了项目的函数优先风格：同一个函数可被 `.py`、link、其它 Python 代码和 LLM 复用，不需要继承工具基类。Python 工具模块通过 `__all__` 明确导出函数，registry 在应用候选模块前逐个检查 `Tool` 生成的完整 schema；模型侧名称加模块命名空间，例如 `image__recognize_image`。

## 基础工具与现有模块

每个 `Chat` 开局默认激活标准 Python 模块 `meta.py`。它的完整模块说明进入工具 system 提示，并导出四个保持无前缀的恢复入口：

| 工具 | 当前效果与边界 |
|---|---|
| `exec_code` | 在 `.py` 的共享 `loc` 中先 `exec(code)`、再 `eval(expr)`；调用时仍检查 op，拥有与 `.py` 接近的进程和宿主机能力。 |
| `list_tools` | 列出 last-good 模块、当前 Chat 的激活状态、最近加载失败和磁盘源码相对 last-good 的新增、修改、删除。 |
| `reload_tools` | 按模块名从磁盘显式应用、更新或删除模块；逐项返回成功和带完整 traceback 的失败结果，失败保留旧 last-good。 |
| `load_tools` | 按模块名把现有 last-good 模块的内容和函数激活到当前 Chat；不读取磁盘。 |

仓库当前提供以下按需模块；函数只在对应模块激活后出现：

| 模块 | 导出能力 |
|---|---|
| `common` | `common__get_time`、`common__poke` |
| `image` | `image__recognize_image`、`image__create_image`、`image__create_image_from_references` |
| `later` | `later__later_add`、`later__later_del` |
| `user_data` | `user_data__get_user_data`、`user_data__set_user_data` |
| `agents` | `agents__assign_tasks` |
| `minecraft` | `minecraft__search_mc_mod`、`minecraft__check_mod` |
| `weather` | `weather__search_city`、`weather__get_realtime_weather`、`weather__get_daily_forecast`、`weather__get_hourly_forecast` |

除 `weather` 继续投影可用的 `mods.weather` 函数（其 schema 描述来自 `mods.weather` 函数自身的 docstring，与命令的 `-h` 帮助同源）外，这些文件持有各自工具的真实实现，不再从 `mods.chat` re-export。图片和子任务模块只惰性复用 `mods.chat` 的 usage/cost 入口，计费状态仍只有一份。

历史源码中有实现、但 `add_tool` 注册被明确注释的 `read_data`、群成员、农历/小六壬、跨窗口发送、`later_list/later_set`、URL 转 CQ 和百科工具，只在 `mods/tools/disable/README.md` 留有决策记录：说明它们当时是什么、现在等价能力在哪里、以及 RAG 等只剩草稿或死名字的项为什么不复活。仓库不保存永不运行的实现；要启用就按现有模块格式在 `mods/tools/` 顶层重写。

`create_image` 使用 OpenAI 原生 `gpt-image-2` 的参数与返回形状，不传 DALL·E 的 `style`、`standard` 或 `response_format=url`。图片以 `b64_json` 返回，解码后使用 `data/tmp_files` 的现有临时图片缓存和过期清理机制；Base64 本身不会进入 QQ 消息或 chatlog。

`create_image_from_references` 的 `image_uris` 参数每行接收一个 URI，并在输入边界按完整 URI 去重。解析后复用统一的内容寻址文件缓存；`file://` 必须是本机绝对路径。所有参考图都需通过图片格式和 20 MiB 上限校验。`gpt-image-2` 会自动高保真处理参考图，请求不传 `input_fidelity`。供应商是否对参考图输入另行计费尚未实测；当前 usage 仍只按实际返回图片数以每张 0.13 元记录。

## 统一模块格式与两种加载

`mods/tools/` 只扫描顶层、不以下划线开头的 `*.py` 和 `*.md`。同 stem 的 Python 与 Markdown 文件冲突；子目录不递归扫描，可以由顶层内容引用或由 Python 正常 import——这是有意的分层而非遗漏：模块目录是常驻上下文，递归会让子文件夹的内容一开局就全部占位，只列顶层则把它们降为"展开之后按需索引"的一级，与首行/全文的分层同理。两种文件共享以下最小格式：第一行是总会出现在模块目录中的描述，后续全部是激活后内容，不设 front matter、summary 或另一套 skill 协议。Markdown 到此结束；Python 还必须显式声明 `__all__`，其中可列零个或多个同步函数。

`mods/tools/__init__.py` 是 loader/registry 包，不是工具格式。`meta.py` 才是已经激活的基础模块：它和其它 Python 模块一样使用第一行 summary、后续说明、普通函数与 `__all__`，但作为必需恢复入口从开局就激活全文，四个函数名不加 `meta__` 前缀。其它顶层模块仍只默认显示第一行，显式激活后才加入余下内容。`meta` 必须精确导出四个恢复函数；删除其磁盘文件后 reload 会失败并继续保留旧 last-good，而不是卸掉恢复入口。

Python 候选作为正常模块执行，可以 import 第三方依赖、其它 `mods` 和同目录下划线 helper。每个导出函数都必须有可用的签名、参数类型标注和 docstring，并通过现有 `Tool` schema 校验；模块内任一导出失败，整个模块都不替换。加载候选不会调用导出函数，但会执行顶层 import 和其它顶层语句，所以这里与 `.py`、命令、link 和宿主机操作属于同一信任域，不是沙箱；顶层应只放 import、常量和定义。

首次创建模块目录提示时，registry 会独立尝试每个文件，成功者成为进程级 last-good，失败者记录 traceback 而不阻断其它模块或 Bot。运行中有两种不同操作：

- `reload_tools(names)` 读取磁盘并逐模块应用变化。成功才原子替换 last-good；失败保留旧版。源文件已删除时，显式 reload 同名模块才删除 last-good。
- `load_tools(names)` 完全不读磁盘，只把 last-good 模块激活到当前 `Chat`。其余模块内容和函数保持不可见。

因此改文件本身不生效，也不会自动产生末尾 hint。`list_tools` 只在被调用时报告 last-good、当前 Chat 激活状态和磁盘差异；没有 watcher。Markdown 与 Python 服从完全相同的手动应用生命周期。活动模块被成功 reload 时，当前 Chat 的内容和工具映射一起更新；其它 Chat 的激活投影不被偷偷改写。

### 追加式上下文

模块目录那条 system 消息是**基线**：`SessionBinding` 在 bind 时渲染一次，之后永不改写。工具变动不回头改它，而是作为新的上下文条目**追加**到末尾——前面的消息一个字都不动，前缀缓存不会被打断。形状取自 deepseek-harness 的 `packages/context/*`：上下文插件从不修改已有消息，首次注入完整基线、之后只追加变更列表，内容包在插件自己拥有的 `<system-reminder>` 框架里。

- **交付点。** `llm.Chat.context_providers` 是一组"每次模型子请求前调用一次、返回要追加的消息"的函数（`Chat.add_context_provider` 注册）。`LLMClient.chat` 在每轮开头调用它们并 `extend` 到 `messages` 末尾。
- **为什么是子请求前而不是立刻。** 工具在 assistant `tool_calls` 消息与 tool result 之间执行；`load_tools`／`reload_tools` 若当场往 `messages` 里插一条 user 消息，就会拆散这一对，供应商会拒。所以 `SessionBinding._announce` 只入队，等到下一轮开头取走，那时 tool result 已经补齐。
- **内容。** 一条通告列出目录新增／移除／描述更新、激活／停用、已激活模块内容更新、以及本次新出现的加载失败；新激活或内容变了的模块附完整正文，失败附 traceback。没有变化就不产生消息。
- **仍然是显式的。** 通告是 `reload_tools`／`load_tools` 的结果报告，不是磁盘变化探测器；没有 watcher，改文件本身仍然不生效。自动加载会把可能正写到一半的模块当成新版本，而 last-good 只在校验通过后替换，就是为了让这种时刻不影响正在跑的会话。要让模型知道磁盘变了，仍然靠 `list_tools` 报告差异——把这一步变成被动提醒是"末尾 hint"那一层的事，见下；即便有了 hint，发现变化之后仍然要显式 reload。
- **累积而非替换。** 上下文里会同时留着某模块的旧正文和后来追加的新正文，靠顺序生效。这是追加式的必然代价（deepseek-harness 的 baseline+refresh 同样如此）；回头改写旧消息就等于放弃前缀缓存。同一轮内的多次变动各自成条、按发生顺序交付，不互相覆盖。
- **形状已验证。** 追加的是 `role="user"`，于是线上会出现 tool 消息紧跟 user 消息、中间没有 assistant 的序列。这在 OpenAI 工具协议下合法（硬性要求只是每个 `tool_calls` id 都有对应 tool 消息），并且实跑验证过：deepseek-v4-flash（2026-09-04），拿模型真实产出的 assistant 消息回放「带 reasoning + tool → user」，普通与流式都是 200；两条退路（改用 `role="system"`、把内容并进最后一条 tool result）同样可用。只测了 deepseek，openai／bytecat 未验证。

- **框架转义。** 模块正文是模型自己写的——`reload_tools` 应用的就是它刚写进磁盘的文件。所以正文里字面的 `</system-reminder>` 会被转义，否则模型可以提前关掉框架，让后面它自己写的内容看起来像系统说的。框架归 `mods/tools` 所有，被框住的内容不许碰它。

同一套 provider 机制也承载 `mods/chat` 的插话队列，见上一节。

### 末尾 hint

hint 与追加式上下文是两层，判据是一句话：**频繁变化、且随时可以重算的状态放 hint；"发生过一次"的事实放 provider。**

- provider 追加进 `Chat.messages`，进历史、可回放、会一直留着。
- hint 每次模型子请求重新渲染一次，只挂在**发出去的那一份**末尾，从不写回 `messages`。旧的自然消失，上下文里不会堆出几代互相矛盾的副本。
- 与 system 提示词一样支持用函数生成（`Chat.add_hint` 接受字符串或可调用对象），只是重置时机不同：system 在建会话时定一次，hint 每个子请求重来一次。
- 因此 hint 里只放随时可重算的东西，不放"只此一次、错过就没有"的信息——那种必须走 provider。

目前唯一的 hint 是 `SessionBinding._drift_hint`：报告 `mods/tools` 磁盘源与 last-good 不一致的模块名。它补上了维护者期望里"模型能主动察觉工具可更新"的那一半——以前这个差异只有模型自己调 `list_tools` 才看得到。三条性质是刻意的：只给名字不给正文（不承载重内容）；只报告不加载（发现变化与决定应用是两步，理由同上）；每次真读磁盘不做缓存（一次 scan 是十来个小文件，相对一次模型往返可忽略，加缓存反而会让"刚改完就问"读到旧值）。文件改回去，提醒就自行消失。


## 一轮工具调用怎样继续

当前调用链是一个同步的自动循环：

```text
Chat.chat
  → LLMClient.chat
  → 请求模型（tool_choice=auto）
  → 收集流式 tool_calls
  → 直接调用普通 Python 函数
  → 追加 assistant tool_calls 与 tool result
  → 带着结果再次请求模型
  → 直到某轮不再调用工具
```

只有模型配置声明 `function_calling=true` 时，工具 schema 才会随请求发送。每次子请求发送前，循环从当前 `Chat.functions` 冻结一份快照；同一份快照同时用于请求 schema 和该响应返回的调用解析。因此 `load_tools` 或 `reload_tools` 在当前任务中执行后，新映射从下一个模型子请求起生效；已经发出的请求不变，同一模型响应中的其它调用仍使用原快照。

流式响应会逐段拼接工具名和 JSON arguments；当前轮模型流完整结束并产出所有普通文本后，才会把收集到的工具调用交给执行层。一次模型响应中的多个工具按收到顺序在当前线程同步执行，不使用事务。

工具返回值统一 `str(result)` 后作为 `role=tool` 消息回传模型。普通工具抛出异常时，类型和错误文本作为工具结果回传，完整 traceback 写应用日志；`reload_tools` 的校验失败则有意把完整 traceback 放进返回值，让模型能够修正模块源码。

每个供应商子响应结束后，完整 `content`、可选 `reasoning_content` 和 `tool_calls` 会合并为一条 assistant 消息加入当前 `Chat.messages`；随后才执行工具并追加 tool result，供下一次模型请求使用。流式文本仍由回调逐段发送：双换行分段会在流中提前产出，末段在流结束时产出，但这些显示片段不再分别写成多条 assistant 历史。工具调用一定在所有可见文本产出之后才执行，因此模型放在工具前的说明会先进入 QQ 发送队列；后续工具失败不会回滚已发文本。assistant 工具消息和 tool result 不会作为独立 QQ 消息写入 chatlog。

若供应商在工具调用响应中返回 `reasoning_content`，流式路径会完整拼接该字段，非流式路径会直接读取它，并把它保留在对应的 assistant `tool_calls` 消息中供所有后续子请求原样回传。这满足 DeepSeek thinking mode 的工具调用协议；思考内容仍只在终端显示，不作为 QQ 回复或独立 chatlog 消息。

循环当前没有最大工具轮数、总执行时限、确认步骤或副作用回滚。模型持续产生工具调用时会继续请求；外部 API、storage 写入、延时任务和代码执行都在工具被调用时立即发生。单次 HTTP 请求有超时（`llm_system/config` 的 `request_timeout`，默认 120 秒，流式响应每收到一个 chunk 重新计时），但整轮循环没有。

### 插话与 `^C` 打断

入站从来没有被生成挡住：`link.dispatch` 在独立线程上，生成期间消息照样到达。以前第二条 at 会**再起一轮并发的 `chat()`**，两轮各自读上下文、各自发言；现在改为一个窗口同时只跑一轮。

登记在 `context.WindowTurn`，键是 `history.window(event)`——**窗口**而不是 `context.interaction_key` 的 `(窗口, 用户)`。因此任何人都能插话、任何人都能 `^C` 打断：LLM 上下文本就按窗口共享，只让触发者能停会让群里其他人无法制止一轮跑偏的生成。这是与 `_waiters` 并列的第二份登记，不是同一份。

- **插话走队列。** 生成期间到达、且进得了上下文的消息由 `chat.capture_chat` 入队；队列在每次模型子请求之前被取走，转换用的是 `chat.event2chat`——与 `get_msgs` 同一个函数，所以插进来的消息与历史消息形状一致，图片和回复引用不会丢。它不打断当前请求。
- **分级。** 只有原本就会触发聊天的消息（at、名字开头、`#poke`、指向 Bot 的戳一戳）才置位 trigger，让本轮结束后再跑一轮回应它；普通消息只进队列不触发，避免普通闲聊把 Bot 拖进无限续聊。`#` 开头的消息一律不入队，与 `get_msgs` 的过滤同理。
- **收尾没有缝。** `context.finish_turn` 在同一把锁里检查 trigger 并注销登记，所以恰好在这一轮结束时到达的 at 要么被这里看到并让它再跑一轮，要么落在注销之后、自己开一轮，不会两头落空。再跑一轮时不补发队列残留：那些消息此刻已经进了 history，下一轮 `get_msgs` 会读到，再追加一次就成了重复。
- **`^C` 打断。** `bot._route` 的 `^C` 分支同时调 `context.cancel`（这条交互线的 waiter）和 `context.cancel_turn`（这个窗口正在跑的那轮）。`LLMClient.chat` 在每轮之间、以及流式读取的**每个 chunk 之后**检查该标记，命中就 `close()` 掉底层 HTTP 流并停止。已经产出的段落已经发进 QQ，收不回来；剩下的半条 assistant 消息直接丢弃，因为每轮都从 history 重建 `messages`，本轮的列表是一次性的。够不到的窗口只剩一个：卡在等待第一个 chunk 时无法打断，要等它到达。

## `assign_tasks` 的子模型分派

`assign_tasks()` 是工具系统内再创建工具系统的高阶能力：父模型传入公共 prompt、多行 tasks、模块名列表、模型名和 `max_workers`，函数为每个 task 新建独立 `Chat`，在线程池中并发运行，最后按原任务顺序返回 `(task, result)`。

子会话和顶层聊天一样获得四个基础工具与模块目录提示，并预先激活父模型明确列出的 last-good 模块；它不自动继承父会话其它活动模块、聊天历史、base 提示或窗口提示词。子会话的模型使用父模型传入的 `provider/model` 字符串，不再另有固定 provider。模块列表可以包含 `agents`，但代码没有强制递归深度、并发上限或全局预算。

函数捕获原始消息，并在每个工作线程运行子 `Chat` 前安装为当前 context，结束时清除；依赖当前窗口的工具因此沿用父任务的聊天事件。子会话仍不拥有父会话的提示词和历史消息。

顶层 `init_chat()` 会增加一次调用计数；子会话不经过 `init_chat()`，因此不会增加同一种顶层次数，但响应 usage 仍会累加 token 成本。`.chattop` 的“调用次数”不能简单理解成实际向供应商发出的全部 HTTP 请求数，工具循环和子任务都可能令一次顶层聊天产生多次请求。

## 当前信任边界与维护取舍

工具调用不是由可信 Python 调用者选择，而是由外部模型根据聊天上下文和提示词生成参数。当前 `init_chat()` 没有按发言者是否为 op 过滤工具，也没有逐工具确认：只要某个私聊或允许群能够触发 LLM，该模型就会看到同一套工具。

这使以下能力处于同一条提示注入路径上：

- `exec_code` 可访问 `.py loc`，本质上接近任意代码执行；
- `reload_tools` 可以在进程内执行并应用 `mods/tools` 中的受信任 Python；候选顶层代码在校验期间就会执行；`load_tools` 可以把任意 last-good 模块交给当前模型；
- `set_user_data` 可读取模型生成的 Python 字面量并修改任意用户 storage；
- `get_user_data` 可把任意用户数据发送给模型供应商；
- 延时任务会在未来执行并回到当前窗口，创建和修改沿用当前发送者权限，不借用 Bot 管理员身份；
- `assign_tasks` 可以增加并发、费用和工具调用深度；
- `poke` 会立即对当前会话产生外部可见的戳一戳动作；
- `recognize_image` 可以下载模型指定的网络图片，或读取模型指定的本机 `file://` 绝对路径，再把图片及识别要求发送给视觉供应商，产生额外网络访问、宿主机文件读取和模型费用；
- `create_image` 会产生按张计费的外部 API 调用，并立即向当前 QQ 会话发送生成结果；
- `create_image_from_references` 还会下载模型指定的网络图片或读取本机 `file://` 绝对路径，并作为 multipart 文件上传给生图供应商；
- 天气和 MC 搜索会向外部站点发请求。

账户相关配置不进入源码或 storage。`main.py` 启动时加载仓库根的 `.env`：生图调用复用 `BYTECAT_BASE_URL` 并读取独立的 `BYTECAT_IMAGE_API_KEY`；天气调用分别读取 `QWEATHER_API_HOST`、`QWEATHER_KEY_ID`、`QWEATHER_PROJECT_ID` 和可选的 `QWEATHER_PRIVATE_KEY_FILE`。可从 [`.env.example`](../.env.example) 复制空白模板；缺少必需值时，调用会显式失败，不会回退到硬编码账户。

这是当前实现事实，但维护者当前接受这条信任模型，不把它列为近期高优先级缺陷：群聊中的 LLM 只应在手动选择的可信群启用，可信群中的聊天内容和外部模型共同处于允许调用这些工具的范围内。群白名单是主要运维控制面，不要求近期为每个工具增加确认、能力对象或独立沙箱。

这条策略有两个需要保持可见的边缘：

- 私聊当前没有 `chat_groups` 白名单，因此“只在可信群开启”并没有覆盖私聊入口；
- 工具注册不按触发者 op 身份变化。若需要额外加固，优先考虑在所有 LLM 入口统一检查触发者是否为 op，而不是重做函数式工具系统。是否启用这项检查尚未决定，文档不能写成当前已有行为。

当前自动工具循环没有最大轮数、总时限或副作用回滚，`assign_tasks` 也没有强制递归深度和全局预算。维护者接受通过 `.reboot` 恢复极端失控调用的运维取舍，暂不据此引入完整预算系统；若真实发生无法靠重启方便恢复的事故，再以该事故为证据增加最小限制。

模块 reload 失败的完整 traceback 会作为结果发给模型供应商。维护者使用可信的正规供应商，并认为 traceback 对模型修正工具有实际价值，因此当前保留这一行为，不把内部路径随请求发送本身列为待修缺陷。普通工具异常只回传类型和错误文本。密钥、聊天正文或额外运行数据仍不应被无意加入异常文本。

值得保留的是：普通函数、签名和 docstring 组成一个可直接复用的能力面。未来若调整边界，应优先修改入口、启用范围或少量检查，而不是仅因工具锋利就建立庞大的工具对象宇宙。
