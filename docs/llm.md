# LLM 聊天、上下文与工具调用

本文描述当前源码中的 LLM 主路径。运行时供应商、模型、价格和提示词来自 storage，本页不复制设备上的真实配置、聊天内容、账号或密钥。工具模块以 `mods.tools` 的进程内 last-good registry 为权威，当前任务能调用哪些函数则由该 `Chat` 的模块绑定决定；模型是否真正收到工具，还取决于 `llm_system/config` 中对应模型的 `function_calling` 标记。

## 入口与状态范围

主聊天入口是 `mods.chat.capture_chat`：`#` 本地子命令先就地执行，其余事件由 `activation_signal()` 判定是否唤醒唯一中心 reader。公开的 `cond()`／`call()` 只保留给可能从 `.py`、动态 link 或 `#hint` 引用它们的旧兼容代码，不再是正式路由的 `chatting` 入口。触发包括：

- @ Bot；
- 以“柚子，”开头的文本；
- 戳一戳 Bot；
- `#...` 控制命令。

群聊只有群号位于 `chat_groups` 时才进入这条路径；私聊没有这层群白名单。普通 `#` 命令先由本地子命令解析，返回的 callable 直接执行而不请求模型；`#poke` 和不能解析成控制命令的普通触发才会进入聊天。`.chat` 命令已删除，`.chattop` 月账命令仍保留。

群内其它成员之间的戳一戳不会单独触发 LLM，但可登记为群聊到达，之后进入候选材料。因此“能被模型看见”与“立即触发模型”是两个不同边界。

中心设置命令直接使用 `#` 前缀，没有 `#agent` 层：`#model`、`#models`、`#prompt`、`#setting`、无参 `#image`、`#reasoning`、`#limit` 和 `#help` 可查看；`#use_model`、`#add_prompt`、`#use_setting`、`#set_setting`、`#del_setting`、`#reset_start`、带参数的 `#image`／`#reasoning`／`#limit` 修改全局设置，要求 op。`#add_prompt` 只接收显式列表。`#hint` 不属于这些全局聊天设置：它由 op 管理，可覆盖本窗口或全局默认，并在最初触发窗口的聊天循环停下时向 QQ 发送状态。

每次中心请求都会新建一个 `Chat` 对象，但共享进程级 `LLMClient`、供应商客户端和 storage。主聊天由唯一Center从冻结材料和长期笔记重建；Center journal记录到达、激活、请求、工具结果与笔记，chatlog保存原始聊天档案。因此：

- 群聊和私聊的到达、激活与实际请求在同一journal中保留各自身份；
- 来源窗口和私聊身份始终保留在输入元数据里，`say` 必须明确目标；
- 主模型、prompt、图片档位、工具状态和预算只从全局 `agent` 设置读取；旧窗口聊天设置不再读取；每窗口 `#hint` 独立保留；
- 主模型运行时不把某个群友事件当作隐式当前窗口；窗口相关工具须显式给目标或不在中心会话中启用。

## 上下文如何组装

正常入口由 `chat.live` 安装Center，每醒冻结近期到达、输出／结果／环境事实及当前note，前置选材可反查并补选冻结原件。默认候选取最近 `max_events` 个到达与事实（两类分别限量），近期画面默认16项；目录是定位，主循环只收到实际选中的原件。每次实际SDK请求连同模型选择、工具schema、已见来源、父笔记版本和cutoff写入同journal。`max_token`检查实际SDK报文的文本估算，超限则拒绝请求，不删除原件或伪造压缩成功。默认40000是本地文本估计上限，不等同供应商token计数；图片不按Base64字符计文本token，视觉token由供应商计算，实际图片payload仍完整留在request。

主循环可以主动 `commit_note(parent,text,sources)`。轮后图书管理员也可核对原件、读运行记录并提交；两者共用同一个CAS入口，必须使用当次请求真正收到的裸来源ID和笔记父版本。目录、文字中嵌套的编号都不授予引用资格；显式全文读后要等下一请求实际曝光，再提交引用。`read_note`读本请求已见版本；恢复旧笔记需先读旧note全文，再用当前parent提交新版本，旧版不被覆写。

`inspect_runtime`按ID区间、种类、owner、request、role查有界目录，目录列记录时间，`after`翻页；每次最多100项。`full=true`只接受单条ID，可读取实际请求、工具结果或旧笔记。`read_archive`／`read_past`取冻结材料；`read_messages`同步查指定窗口本地档案，正文随工具结果进入下一子请求。原件和note历史保留；引用可追溯不证明语义正确。

`edit_hint`、工具模块last-good、TTL与按请求冻结callable仍沿用原机制。`take`／`fetch`／`mark_read`／`cover_events`等旧未读／覆盖工具不向正式Center提供；旧oplog、reader和离线接口保留历史用途。每醒重新选材，不再持久固定历史起点，`#reset_start`不修改新循环。

中心主聊天的图片档位由 op 用 `#image <mode>` 全局设置。名称与数字别名分别为 `off/0`、`lazy/1`、`eager/2`；历史布尔值兼容为 `False → off`、`True → lazy`。`off` 会把历史消息中的图片 part 降级为 `[图片(URI)]` 文本：避免把 `image_url` 发给纯文本模型。中心 agent 可按需加载 `image` 模块：`recognize_image` 不依赖窗口，生图返回临时文件 URI，由模型再用带显式目标的 `say` 发送。`lazy` 只在 LLM 聊天实际触发时处理本轮上下文中的图片；**一次对话内同一张图只解析一次**——这里的“一次对话”指 `chat.chat()` 的一次持有（多轮工具调用与插话续写都算同一轮，直到它的 `finally`），解析失败的不再逐轮重试（腾讯 `rkey` 过期后只会拿到 HTML），成功的也直接复用，对话结束后重新聊会重新检查。这项检查台账挂在线程局部，eager 预取另在线程记账。`eager` 在图片消息到达时立即启动后台下载：中心聊天模型能直接读图时只缓存原图，纯文本模型才会同时预生成文字描述。同一来源若恰好同时被 eager 和聊天请求，描述生成会等待同一个进行中任务，避免重复计费。纯文本模型的识别结果统一压成一个 `[图片(URI)识别结果：描述]` 文本 part，明确 URL 与描述属于同一张图片；视觉模型收到的实际图片 part 前会保留一段“下方图片的原始链接”文本。图片加载失败或没有可用视觉模型时也使用同一个带 URI 的单段格式。

纯文本图片在实际请求的转换阶段生成描述，视觉请求以独立vision角色记录实际payload、输出与usage；缓存命中不会伪造一次模型请求。下一醒仍从原件重新组装，描述可能因缓存或源文件变化而不同；实际request保留当时发送内容。`attach_image`在不支持工具结果带图的模型上仅供当前请求使用。

自动描述使用固定 prompt，要求直接概括可见内容、转录重要文字、标明不确定项，并禁止“如果你愿意我还可以……”一类元话术。自动聊天图片处理、eager 预取、`recognize_image` 和参考图生图共用图片解析入口：`http://`、`https://` 和本机绝对 `file://` URI 都解析为 SHA-256 内容身份；网络图片按摘要缓存在 `data/tmp_files`，本地文件只计算摘要而不复制。所有入口都执行图片格式和 20 MiB 上限校验。需要把图片交给视觉模型时，再统一由 `image_uri_to_data_uri` 编码。`recognize_image` 支持自定义识别 prompt，不设置输出 token 上限，也不读写自动描述缓存，避免长文本识别被截断以及不同识别任务互相串用答案。

视觉模型实际收到图片前会检查已经编码的图片尺寸。若图片高度严格大于宽度的 4 倍，原图会被仅在内存中切成从上到下排列的多个 PNG 片段：每片从 3 倍图宽的整数倍高度开始，先覆盖 3 倍图宽；若后续仍超过 1 倍图宽则向后延伸四分之一图宽作为重叠，否则直接延伸到末尾，因此片段最高为 4 倍图宽。这些片段替代原图，并附带 `【自动图片切割】` 提示，让模型按一张连续图片理解并避免重复转录。原生视觉聊天和为纯文本模型生成图片描述共用这项处理；分片不写入文件或缓存，自动描述仍按原始图片的内容摘要缓存最终结果。生图参考图不经过这项识别预处理。

新生成的描述缓存以图片 SHA-256 为键，值包含 `description`、`cached_at` 和自动描述 prompt 版本。迁移脚本也能保留 QQ URL 自带内容摘要的历史描述：`multimedia` 使用 `sha1:<摘要>`，`gchat` 使用 `md5:<摘要>`；这些迁移键只用于命中描述，不伪装成可解析原图的 alias。只有视觉调用成功才写入新描述；lazy 和 eager 对字节相同的图片直接复用文字，即使来源 URL 不同。进行中识别也以“描述缓存对象、内容摘要、prompt 版本”合并。

摘要是内部身份，不是模型或工具参数。原始 URI 始终保留在 `[图片(URI)]`、`[图片(URI)识别结果：描述]` 或视觉图片前的“原始链接”文本中，模型调用 `recognize_image`、`create_image_from_references` 等工具时继续传这个 URI；解析入口再用 URI alias 透明定位本地 SHA-256 文件。因此更换 `rkey` 不会改变同一 QQ 图片的来源身份，已有本地 alias 时也不必用旧链接重新下载。

`llm_system/image_uri_aliases` 保存来源 URI 到内容摘要的限时映射。QQ `multimedia.nt.qq.com.cn/download` 来源键只使用 `appid + fileid`，不把临时 `rkey` 当作图片身份；其它网络来源使用规范化完整 URL 的摘要；本地文件来源键还包含路径、大小和修改时间。alias、内容文件和描述分别按最后使用时间清理，互不作为永久引用。网络原图以 SHA-256 文件名保存在 `data/tmp_files`，命中时刷新文件时间；URI 解析活动会让内容文件和 alias 至多每 24 小时清理一次，自动描述处理活动会让描述至多每天清理一次，三者都以超过 15 天未使用为过期条件。Cave 使用的 `data/images` 永久图片不参与清理。

旧 URL-MD5 文件和 URL 键描述不由运行时兼容。升级前应停止 Bot，先运行 `python3 scripts/migrate_image_cache.py` 查看聚合报告，再按报告处理无法迁移项并使用 `--apply` 切换；脚本不访问网络，也不打印来源 URL 或描述正文，并在 `data/migrations/` 建立迁移备份。控制台在每次请求前打印完成图片转换后的最终消息，因此文本描述、原始 URI 和视觉模型实际收到的 Data URI part 都可核对；Data URI 只在日志中保留前 80 个字符并标明省略长度，发送给模型的数据不截断。

## 供应商与模型能力

`LLMClient` 从 `llm_system/config` 读取：

- providers、base URL 和 API key；配置值可以引用环境变量名；
- 每个 provider 的 models：只提供价格与能力元数据，不是可用模型的白名单；
- `provider/model` 形式的默认模型和视觉模型；
- vision、function_calling、价格等模型属性：输入（未命中）、输入（命中缓存）与输出三段基础单价，单位是元/百万 token；
- 可选的 `price_fn`：放在 provider 中供其模型共享，或放在单个模型中覆盖；模型设为 `null` 可取消继承。没有函数就直接用基础单价。

`price_fn` 是保存在 JSON 中的 Python 函数源码，必须定义 `price_fn(when, prices)` 并返回三项单价字典。`when` 是带 UTC 时区的请求发起时间，可用提供的 `ZoneInfo` 转为供应商时区；`prices` 是基础单价的副本，键为 `prompt_price`、`prompt_cached_price`、`completion_price`。函数可按任意条件计算；返回值必须保留三个键且是有限非负数。配置是 Bot 的宿主机信任域，函数会直接执行。DeepSeek 的默认函数见 `mods/llm/models.py`，含 2026 年假期日期；已有的 `llm_system/config` 不会自动合并默认函数。仍在运行旧代码的实例需要暂留 `off_peak` 供旧进程计价；新代码在有 `price_fn` 时只用函数，重启后可删掉旧字段。只有旧字段而没有函数时，新代码会明确报迁移错误。未来假期依官方公告更新该函数。[DeepSeek 价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[国务院 2026 年节假日安排](https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm)。

任何聊天窗口都可用 `#model` 和 `#models` 查看全局选择及其模型列表；`#use_model` 修改全局选择时要求 op。`#use_model <provider>/<model>` 会保存一个完整模型选择，并且只以参数中第一个 `/` 为分隔，所以模型名本身可以继续包含 `/`；不带参数的 `#use_model` 重置该选择。只有**供应商**必须已配置：没有 base URL 与 API key 就不存在可调用的对象，命令会拒绝它。**模型不必登记在配置里**：未登记的 `provider/model` 原样下传，本地按「支持视觉与函数调用、价格 0」处理（`models.UNKNOWN_MODEL_CAPABILITIES`），模型是否真的存在由对端回答。所以打错的模型名拿到的是一条供应商报错，而不是本地的拒绝。`#models` 先向对端请求它当前提供的模型列表，取不到（网络、密钥或对端不实现 `/models`）就退回本地配置并在末尾说明已回退；本地没有元数据的行只显示名字（价格为 `-`），能力标记留空；价格列读的是「输入未命中 / 输入命中 / 输出」，显示按当前时刻调用 `price_fn` 后的单价。这些取舍见[模型选择的宽松解析](working/proposals/model-selection.md)。源码内的默认配置只用于首次创建空配置，不能代表当前设备正在使用的服务。

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

每个 `Chat` 开局默认激活标准 Python 模块 `meta.py`。完整模块说明在当前Chat中可见，实际发出的版本随request记录；导出的工具一律不带前缀，其中四个是**必需**的恢复入口（少一个模型就没法自救）：

| 工具 | 当前效果与边界 |
|---|---|
| `exec_code` | 在 `.py` 的共享 `loc` 中先 `exec(code)`、再 `eval(expr)`；`timeout` 必填（秒，`0` 表示不限），代码跑在一个可终止的子线程里，到点终止它启动的子进程并中断该线程。调用时仍检查 op，拥有与 `.py` 接近的进程和宿主机能力。 |
| `list_tools` | 列出 last-good 模块、当前会话激活状态、空闲回收规则、最近加载失败及磁盘差异。 |
| `reload_tools` | 按模块名从磁盘显式应用、更新或删除模块；逐项返回成功和带完整 traceback 的失败结果，失败保留旧 last-good。 |
| `load_tools` | 将 last-good 模块激活到当前 Chat；中心名单存全局 `agent`，不读取磁盘。 |

仓库当前提供以下按需模块；函数只在对应模块激活后出现：

| 模块 | 导出能力 |
|---|---|
| `common` | `common__get_time`、`common__poke` |
| `image` | `image__recognize_image`、`image__create_image`、`image__create_image_from_references` |
| `later` | `later__later_add`、`later__later_del` |
| `user_data` | `user_data__get_user_data`、`user_data__set_user_data` |
| `agents` | `agents__assign_tasks` |
| `amap` | `amap__login`、`amap__search`、`amap__around` 等地点与路线查询 |
| `host` | `host__read_file`、`host__write_file`、`host__run_command` |
| `minecraft` | `minecraft__search_mc_mod`、`minecraft__check_mod` |
| `weather` | `weather__search_city`、`weather__get_realtime_weather`、`weather__get_daily_forecast`、`weather__get_hourly_forecast` |

`host` 是 `exec_code` 之外的常用出口：文件读写和 shell 都不必再绕一次任意代码执行。读取按本次实际截取的窗口计字节，超过 `max_bytes` 时只回大小和后续建议（缩小区间、先 `grep -n` 定位、或用 `assign_tasks` 开子会话通读后带回摘要），不做分页会话。改写靠 `read_file` 首行 header 里的 `line`/`size`/`v` 定位与对账：位置与新内容的写法无关，`v` 只在该区间自读取后被改动过时失配，失配的返回自带现状和新 header。渲染格式与 `.edit` 同源，所以手动编辑和模型改写看到的是同一种东西；它没有目录白名单，`data/`、`config.json`、`.env` 和聊天记录都在可及范围内。

除 `weather` 继续投影可用的 `mods.weather` 函数（其 schema 描述来自 `mods.weather` 函数自身的 docstring，与命令的 `-h` 帮助同源）外，这些文件持有各自工具的真实实现，不再从 `mods.chat` re-export。图片和子任务模块只惰性复用 `mods.chat` 的 usage/cost 入口，计费状态仍只有一份。

历史源码中有实现、但 `add_tool` 注册被明确注释的 `read_data`、群成员、农历/小六壬、跨窗口发送、`later_list/later_set`、URL 转 CQ 和百科工具，只在 `mods/tools/disable/README.md` 留有决策记录：说明它们当时是什么、现在等价能力在哪里、以及 RAG 等只剩草稿或死名字的项为什么不复活。仓库不保存永不运行的实现；要启用就按现有模块格式在 `mods/tools/` 顶层重写。

`create_image` 使用 OpenAI 原生 `gpt-image-2` 的参数与返回形状，不传 DALL·E 的 `style`、`standard` 或 `response_format=url`。图片以 `b64_json` 返回，解码后存进 `data/tmp_files` 临时图片缓存，工具只返回 `file://` 路径，不自动发送；Base64 本身不会进入 QQ 消息或 chatlog。缓存文件闲置超过 15 天可能清理。

`create_image_from_references` 的 `image_uris` 参数每行接收一个 URI，并在输入边界按完整 URI 去重。解析后复用统一的内容寻址文件缓存；`file://` 必须是本机绝对路径。所有参考图都需通过图片格式和 20 MiB 上限校验。`gpt-image-2` 会自动高保真处理参考图，请求不传 `input_fidelity`。供应商是否对参考图输入另行计费尚未实测；当前 usage 仍只按实际返回图片数以每张 0.13 元记录。

退役窗口会话后，四个原本依赖隐式来源的模块改用中心可调用的边界：`image` 生图只回 `data/tmp_files` 内容缓存的 `file://` 路径，模型再以 `[CQ:image,file=...]` 通过 `say(target="g…"/"u…")` 发送，`say` 的 CQ 正文不转义；`later` 增删任务必须给明确目标窗口；`amap` 的 Key 存在 Bot 自己的用户 storage，不再按唤醒作者查；`agents__assign_tasks` 的子会话清除当前事件、不继承隐式窗口，结果只作为父工具返回。`common__poke` 也要求中心会话明确目标。Bot 非 op 时仍可经 `say` 发送已生成的本地图片，但读取任意本地 `file://` 参考图或识别图仍需 op。

## 统一模块格式与两种加载

`mods/tools/` 只扫描顶层、不以下划线开头的 `*.py` 和 `*.md`。同 stem 的 Python 与 Markdown 文件冲突；子目录不递归扫描，可以由顶层内容引用或由 Python 正常 import——这是有意的分层而非遗漏：模块目录是常驻上下文，递归会让子文件夹的内容一开局就全部占位，只列顶层则把它们降为"展开之后按需索引"的一级，与首行/全文的分层同理。两种文件共享以下最小格式：第一行是总会出现在模块目录中的描述，后续全部是激活后内容，不设 front matter、summary 或另一套 skill 协议。Markdown 到此结束；Python 还必须显式声明 `__all__`，其中可列零个或多个同步函数。

`mods/tools/__init__.py` 是 loader/registry 包，不是工具格式。`meta.py` 才是已经激活的基础模块：它和其它 Python 模块一样使用第一行 summary、后续说明、普通函数与 `__all__`，但作为必需恢复入口从开局就激活全文，四个函数名不加 `meta__` 前缀。其它顶层模块仍只默认显示第一行，显式激活后才加入余下内容。`meta` 必须**至少**导出那四个恢复函数（`exec_code`/`list_tools`/`reload_tools`/`load_tools`），并同时承载始终可用的聊天、信源和记忆工具；校验只读「少了哪个」（`_BASE_TOOL_NAMES` 与 `absent` 判定），不冻结额外导出数量。原先要求 `__all__` 与那四个完全相等是迁移带来的附带收紧；基础模块还要增加其它始终可用能力，精确相等会让整个恢复入口加载失败。删除其磁盘文件后 reload 会失败并继续保留旧 last-good，而不是卸掉恢复入口。

模块可以在顶层写 `BOT_OP_ONLY = True`，声明"只有 Bot 自身拥有 op 权限时可见、可加载"（目前只有 `op.py` 这么做）。Bot 的权限来自 `config.bot_permissions.op`，在一次进程生命周期内固定，不读取 `context.current()`，因此不会因这一轮读到谁的消息而出现或消失。三层都拦：渲染目录时过滤（`tools.bot_op_tool_visible`）、`SessionBinding.load` 拒绝（模型记得名字直接 load 也没用）、工具执行时再查一次 `op.bot_is_op()`。

中心 reader 的模型与辅助调用记入 Bot 的全局月账，不随唤醒作者或 `context.current()` 改变；旧月份不迁移。`cmds__run_command` 留空 `sender` 时以 Bot 自身执行，显式代行人类 op 时 Bot 自身也必须有 op 权限；下游仍按被代行者判权。`message.recvmsg(sender_id=X)` 只在可编程环境中使用，不直接暴露为模型工具。

Python 候选作为正常模块执行，可以 import 第三方依赖、其它 `mods` 和同目录下划线 helper。每个导出函数都必须有可用的签名、参数类型标注和 docstring，并通过现有 `Tool` schema 校验；模块内任一导出失败，整个模块都不替换。加载候选不会调用导出函数，但会执行顶层 import 和其它顶层语句，所以这里与 `.py`、命令、link 和宿主机操作属于同一信任域，不是沙箱；顶层应只放 import、常量和定义。

首次创建模块目录提示时，registry 会独立尝试每个文件，成功者成为进程级 last-good，失败者记录 traceback 而不阻断其它模块或 Bot。运行中有两种不同操作：

- `reload_tools(names)` 读取磁盘并逐模块应用变化。成功才原子替换 last-good；失败保留旧版。源文件已删除时，显式 reload 同名模块才删除 last-good。
- `load_tools(names)` 完全不读磁盘，只把 last-good 模块激活到当前 Chat；中心实际加载名单写全局 `agent.active_tools`。曾发送过的函数定义即使卸载仍保留，但调用只返回重装提示。

因此改文件本身不生效；末尾 drift hint 只报告磁盘差异，不自动加载。`list_tools` 在调用时报告 last-good、当前激活状态和磁盘差异；没有 watcher。Markdown 与 Python 服从同一手动应用生命周期。成功 reload 替换进程级 last-good；中心会话在下一请求边界同步共享 registry，再登记变化。

### 激活属于会话主体

中心 agent 的实际加载集合保存在全局 `agent.active_tools`，每个模块保存最后装入或调用的时刻；`SessionBinding.restore` 在新一轮开局按它恢复，闲置超过 1 小时的模块不再激活。`touch` 在工具结果后立即写回使用时刻。权限暂时不足的模块保留原时刻在激活名单里，恢复权限后仍可装回；源码已删除的模块才从名单删除。子代理不继承中心激活名单。

函数定义的生命周期与激活分开：`agent.tool_schema_modules` 保存模块首次加载的顺序；卸载后定义仍在请求中，但调用只返回先 `load_tools` 的提示。重启从当前 last-good 代码恢复定义，不保存旧 schema。调用同一请求里的函数始终使用请求开始时冻结的 callable；共享 registry 经其他会话 reload 后，中心在下一请求边界同步本会话函数再告知变化。`list_tools` 返回完整当前状态和磁盘差异。

### 追加式上下文

主循环每个请求前同步实际registry、工具状态、当前note和新环境事实，变动追加到当前Chat。实际发出的完整payload存入Center request，可反查当时模型看到什么。临时续接与磁盘源码差异作为当次末尾hint，不写回Chat.messages；模块正文中的 `</system-reminder>`仍转义。

### 末尾 hint

hint 与追加式上下文是两层，判据是一句话：**频繁变化、且随时可以重算的状态放 hint；"发生过一次"的事实放 provider。**

- provider 追加进 `Chat.messages`，进历史、可回放、会一直留着。
- hint 每次模型子请求重新渲染一次，只挂在**发出去的那一份**末尾，从不写回 `messages`。旧的自然消失，上下文里不会堆出几代互相矛盾的副本。
- 与 system 提示词一样支持用函数生成（`Chat.add_hint` 接受字符串或可调用对象），只是重置时机不同：system 在建会话时定一次，hint 每个子请求重来一次。
- 因此 hint 里只放随时可重算的东西，不放"只此一次、错过就没有"的信息——那种必须走 provider。

中心待办使用 `edit_hint` 保存为可重算的全局末尾 hint，而不是逐版挂到模型末尾；完成后整体更新或清空。长期可复用经验可写成 `mods/tools/*.md` Skill，经 `reload_tools` 应用、`load_tools` 按需插入。Skill 不代替原话索引：结论保留实际来源ID，原话由 `read_archive`／`read_past`／`inspect_runtime`反查。

`SessionBinding._drift_hint` 报告 `mods/tools` 磁盘源与 last-good 不一致的模块名；只报告不加载，也不重复工具正文。它每次请求重读磁盘，文件改回去，提醒就自行消失。


### 中心工具与设置

新Center从全局 `agent.active_tools`恢复工具，保留模块TTL、`agent.tool_schema_modules`的定义顺序以及last-good行为；每请求同步registry后移除已退休的未读／覆盖工具，再加入Center原生笔记、轨迹、反查与重访函数。实际schema随request记录；工具调用仍按发请求时冻结的callable执行。

`image`、`reasoning`、`max_events`、`max_token`、`pressure_percent`仍使用全局归一化设置，op通过原 `#` 命令修改。默认事件数500、token40000；新模式中事件数约束候选范围、token约束实际请求的文本估算，`#reset_start`不再安排固定起点。窗口 `#hint`独立保留，旧窗口聊天配置不合并。候选范围不保证逐条标新旧，也不自动查完旧档。

### 运行轨迹与失败

`data/agent-memory/center.jsonl`为新Center唯一事实日志，条目使用 `C…` ID、链式哈希和UTC记录时间；输出先落盘，再登记行动 `C…#位置`并派发工具，整批结果如实追加。完整原生assistant/tool配对留在当前Chat；跨醒按材料重新投影。来源窗口、原始chatlog和笔记版本均不因画面裁剪而删除。

落盘失败或派发结局未知会关闭后续请求／行动入口，重启不自动重放副作用；普通已知供应商失败写失败结束，可由后续新激活继续。选材、整理、委派和视觉各有独立role/request，不能替主循环确认激活；主循环与后台整理共用笔记CAS而非两份状态。长期journal目前全量恢复入内存，仍需观察增长，不以有界查询宣称解决了存储资源问题。

每天北京时间04:00可进行一次同主体自检，默认quiet，不强制报告或改记忆；实际请求、结果和源文件可供自己查证。它使用现有exec／文件／工具reload／完整重启能力，不引入独立调试主体或新沙箱。

### 思考内容开关

主会话的思考载体模式由 op 用 `#reasoning` 全局设置。`keep`是默认值，`drop`不把思考带回当前会话工具续请求。

本次Chat的完整原生配对按该开关处理；Center输出仍记录供应商实际返回的思考，可用明确ID反查。跨醒材料由Center重新投影，不再沿旧cover或固定起点管理。

### 窗口的结束提示（`#hint`）

这是与上面[末尾 hint](#末尾-hint)一节不同的另一个「hint」，两者只是碰巧同名：那一节说的是**发给模型的那一份请求末尾**挂什么，这里说的是**用户存给某个聊天窗口的一段代码**，它会在这个窗口的聊天循环停下时被求值并发送。

命令面只做文本管理——写、看、开关，和 `#model`/`#prompt` 一系；「它在聊天结束时自动跑」是另一回事，两半分开看。

- **谁跑、什么时候跑。** 唯一中心 reader 在 `_drive_agent` 的 `finally` 求值一次，按最初触发窗口选择窗口 `#hint` 配置；这是向 QQ 发状态的旧机制，不是全局模型待办 hint。无 reader 所有权的早退不触发。
- **配置模型。** 两份字典——全局默认 `storage.get("", "hint")` 与窗口 `getchatstorage()["hint"]`——按显式顺序合并成 `{**default, **window}`，两侧都只认同名的 `code`/`on` 两个键，没有别的间接层。发不发 = 合并结果里 `code` 非空**且** `on` 为 `True`；`on` 缺失按 `False`。窗口可以只写 `on`（单独关掉默认提示，`code` 仍继承）。`#hint set <源码>` 写入 `{"code": …, "on": True}`，`#hint set` 无参清掉窗口配置、回落默认；`#hint` 与 `#hint default` 只翻开关，保源码。没有 `del`——源码是劳动成果，toggle 不该顺手删掉它。
- **求值。** 走 `py.eval_last(source, environment)`，与 link 共用同一份实现（原先 link 里那份私有副本已消掉）：前面各行 `exec`，末行 `eval`；末行为空、以 `#` 开头、或结果为 `None` 就不发。环境是共享动态环境 `py.loc` 的**一份副本**，另注入 `window`（本窗口的 `history.window` 键）、`usage`（最后一次中心请求的事件文本 token 估算）、`event_count`（该请求中不同正式事件号的数量）与 `context_limit`（该请求实际使用的事件数/token 上限）。后三项在中心 agent 仍活跃时冻结，避免结束后误读不相关的窗口状态。
- **输出带 `#` 前缀**（`"#" + cq.escape(str(result))`），因此不回流进模型上下文，也不自指。报错同样以 `#` 开头、附 traceback。
- **异常安全是硬要求。** 求值与发送的任何异常都在这一层吞掉、只写 `hint` 日志流，**绝不抛回 `finally`**：这段代码是用户自己写的、每次聊天都自动跑，让它抛出去就等于一段烂代码能污染聊天主流程的返回路径。（只吞 `Exception`——`SystemExit`/`KeyboardInterrupt` 是进程控制，不该被用户代码吃掉。）
- **「已用上下文」的出口是 `chat.context_usage()`**。Center记录本次Chat结束时messages的本地token估算，不含另挂的schema或临时hint，也不是供应商实际token数。
- **op 专属。** hint 是用户可写、跑在特权环境、还每次聊天自动执行的代码，权限与 `.py`/`.link` 同级，比只改提示文本的 `#prompt` 高一档。非 op 的 `#hint` **不接管**（`cond()` 里先判权，返回 `False`），只给一节流提醒——复用 `op.require_op` 的约定，提醒的节流按「同一个人的同类重试」判（`pattern=r"^#\s*hint"`），所以提醒不会把别人正常的 `#hint` 也算进去。
- **`#help [名称]`** 照 `.help` 的两级形态：无参列所有 `#` 子命令的首行摘要（`#{pattern} — {首行}`），带名称显示那一条的完整多行说明，查不到就是 `该命令不存在！`。**不做 op 过滤**——单 `#help` 列出全部（含 op 专属的 `hint`），非 op 真去执行 `#hint` 时才被拒：`#help` 是所有人的发现入口，为它加特判不值。

命令目录见 [commands.md](commands.md)；取舍、被排除的做法与推翻条件见[窗口级 `#hint`](working/proposals/chat-hint.md)。

## 一轮工具调用怎样继续

当前调用链是一个同步的自动循环：

```text
Chat.chat
  → LLMClient.chat
  → 请求模型（tool_choice=auto）
  → 收集流式 tool_calls
  → 直接调用普通 Python 函数
  → 登记带原生 assistant 的 O，执行行动并登记一个 R
  → 完整的 DeepSeek O/R 原生配对留在上下文；其余使用已编号文本投影
  → 直到某轮不再调用工具
```

只有模型配置声明 `function_calling=true` 时，工具 schema 才会随请求发送。每次子请求发送前，循环从当前 `Chat.functions` 冻结一份快照；同一份快照同时用于请求 schema 和该响应返回的调用解析。因此 `load_tools` 或 `reload_tools` 在当前任务中执行后，新映射从下一个模型子请求起生效；已经发出的请求不变，同一模型响应中的其它调用仍使用原快照。

流式响应会逐段拼接工具名和 JSON arguments；当前轮模型流完整结束并产出所有普通文本后，才会把收集到的工具调用交给执行层。一次模型响应中的多个工具按收到顺序在当前线程同步执行，不使用事务。

工具返回值统一 `str(result)`；普通工具抛出异常时，类型和错误文本作为结果回传，完整 traceback 写应用日志；`reload_tools` 的校验失败则有意把完整 traceback 放进返回值，让模型能够修正模块源码。名字不在本轮快照里、或参数不是合法 JSON 的调用不再被丢掉，而是换成说明性结果（见上面「叫不到的名字不再吞掉调用」）。

完整子响应先登记Center output，再派发工具并登记整批results。原生assistant/tool配对留在本次会话；下一子请求追加实际返回与新环境事实，跨醒重新选材。流式文本仍由回调逐段打印，普通工具失败不回滚已发生行动，`PersistenceError`立即停批。供应商实际返回的 `reasoning_content`保存在output，QQ回复和chatlog不存思考。

循环当前没有最大工具轮数、总执行时限、确认步骤或副作用回滚。模型持续产生工具调用时会继续请求；外部 API、storage 写入、延时任务和代码执行都在工具被调用时立即发生。单次 HTTP 请求有超时（`llm_system/config` 的 `request_timeout`，默认 120 秒，流式响应每收到一个 chunk 重新计时），但整轮循环没有。

### 插话与 `^C` 打断

入站、link和原始chatlog不被模型生成挡住；Center记录新到达和激活，唯一reader仍用 `AGENT_WINDOW`。后来激活可在当前轮下一请求看到；通知确认不等于逐条看完消息。环境／行动回执也可追加同journal，本轮请求已经发出的payload不被改写；不支持抢占在途HTTP。

相关窗口 `^C`仍取消turn并触发watchdog，模型在轮间、流式chunk和工具调用前检查停止。watchdog仅跟踪一次工具调用期间创建的子进程，不接管其后常驻进程；同步工具和不能杀掉的系统调用仍有原有限制，不能把软停止当作事务回滚。新Center不会重放已派发副作用；完整结果保留，未知派发需核对。

## `assign_tasks` 的子模型分派

`assign_tasks()` 是工具系统内再创建工具系统的高阶能力：父模型传入公共 prompt、多行 tasks、模块名列表、模型名和 `max_workers`，函数为每个 task 新建独立 `Chat`，在线程池中并发运行，最后按原任务顺序返回 `(task, result)`。Center模式下每个子任务绑定独立owner与role，实际请求／输出／工具结果记入同journal，不继承主循环来源信用。

子会话和顶层聊天一样获得四个基础工具与模块目录提示，并预先激活父模型明确列出的 last-good 模块；它不自动继承父会话其它活动模块、聊天历史、base 提示或窗口提示词。子会话的模型使用父模型传入的 `provider/model` 字符串，不再另有固定 provider。模块列表可以包含 `agents`，但代码没有强制递归深度、并发上限或全局预算。

子会话清除当前消息 context，不继承隐式 QQ 窗口；需要发消息或建任务的工具必须接收显式目标。它仍不拥有父会话的提示词和历史消息。

每次顶层 `_activate_chat()` 增加一次调用计数并从全局 `agent` 恢复中心主体的持久工具名单。子会话不经过顶层激活，响应 usage 仍会累加费用；中心主模型与辅助调用都记到 Bot。当前费用只在供应商返回 usage 时按输入未命中／缓存命中／输出三段入账；供应商不返回 usage 时尚无发送 token 兜底。单价来自模型元数据，峰谷取请求发起时刻；缓存命中价单独计，两边都不报命中数时保守按未命中算。`.chattop` 按自然月记账，中心费用以 Bot QQ 号显示；“调用次数”并不等于所有底层 HTTP 子请求数。

## 当前信任边界与维护取舍

工具调用不是由可信 Python 调用者选择，而是由外部模型根据聊天上下文和提示词生成参数。模型发起的工具调用属于 Bot 自己的行动：需要宿主级权限的入口统一读取固定的 `config.bot_permissions.op`，不借最后一个触发者或当前读到的消息作者。`BOT_OP_ONLY` 模块（目前只有 op 工具集）也按同一声明决定目录可见性和加载；其它模块仍对所有 LLM 轮显示，但其中需要 op 的函数会在调用时检查 Bot 自身权限。没有逐工具确认。

这使以下能力处于同一条提示注入路径上：

- `exec_code` 可访问 `.py loc`，本质上接近任意代码执行；
- `host` 的三个函数可以读、改、删宿主机任意路径的文件并执行任意 shell 命令，与 `exec_code` 同级；读取会把文件内容发送给模型供应商，写入和命令都不可撤销；
- `reload_tools` 可以在进程内执行并应用 `mods/tools` 中的受信任 Python；候选顶层代码在校验期间就会执行；`load_tools` 可以把任意 last-good 模块交给当前模型；
- `set_user_data` 可读取模型生成的 Python 字面量并修改任意用户 storage；
- `get_user_data` 可把任意用户数据发送给模型供应商；
- `later__later_add`／`later__later_del` 要求明确 `g`／`u` 目标；未来任务归属该目标窗口，任意代码按 Bot 自身权限判定，触发时不重新换身份；人类直接使用 `.later` 仍按消息作者判定；
- `assign_tasks` 可以增加并发、费用和工具调用深度；
- `common__poke` 要求明确目标窗口，会立即产生外部可见的戳一戳动作；
- `recognize_image` 可以下载模型指定的网络图片，或读取模型指定的本机 `file://` 绝对路径，再把图片及识别要求发送给视觉供应商，产生额外网络访问、宿主机文件读取和模型费用；
- `create_image` 会产生按张计费的外部 API 调用，返回可能过期的临时本地文件路径；
- `create_image_from_references` 还会下载模型指定的网络图片或读取本机 `file://` 绝对路径，并作为 multipart 文件上传给生图供应商；它同样只返回临时路径，发送须另用 `say` 明确目标；
- 天气和 MC 搜索会向外部站点发请求。

账户相关配置不进入源码或 storage。`main.py` 启动时加载仓库根的 `.env`：生图调用复用 `BYTECAT_BASE_URL` 并读取独立的 `BYTECAT_IMAGE_API_KEY`；天气调用分别读取 `QWEATHER_API_HOST`、`QWEATHER_KEY_ID`、`QWEATHER_PROJECT_ID` 和可选的 `QWEATHER_PRIVATE_KEY_FILE`。可从 [`.env.example`](../.env.example) 复制空白模板；缺少必需值时，调用会显式失败，不会回退到硬编码账户。

这是当前实现事实，但维护者当前接受这条信任模型：群聊中的 LLM 只应在手动选择的可信群启用，可信群中的聊天内容和外部模型共同处于可以影响 Bot 判断的范围内。按触发者切换权限挡不住混合上下文里的提示注入，却会让同一串工作因消息先后掉权限，所以这里选择一份固定 Bot 权限，不为每个工具增加确认、能力对象或独立沙箱。

这条策略有两个需要保持可见的边缘：

- 私聊当前没有 `chat_groups` 白名单，因此“只在可信群开启”并没有覆盖私聊入口；
- `config.bot_permissions.op=false` 会让 `exec_code`、`host`、本地文件读取、任意用户数据修改、任意代码延时任务和 Bot 身份命令拒绝执行，并隐藏 `BOT_OP_ONLY` 模块；它不会按窗口或消息作者变化。修改这项配置需要完整重启。
- 显式代行是另一类行为：`cmds__run_command(sender=X)` 构造作者为 X 的事件，后续普通命令按 X 的权限执行；Bot 自身没有 op 权限时拒绝代行人类 op，不能借 X 重新打开宿主能力。`message.recvmsg(sender_id=X)` 是可编程环境中的函数，不直接作为模型工具暴露，调用它的模型代码仍需经过 Bot 自身 op 门。

当前自动工具循环没有最大轮数、总时限或副作用回滚，`assign_tasks` 也没有强制递归深度和全局预算。维护者接受通过 `.reboot` 恢复极端失控调用的运维取舍，暂不据此引入完整预算系统；若真实发生无法靠重启方便恢复的事故，再以该事故为证据增加最小限制。

模块 reload 失败的完整 traceback 会作为结果发给模型供应商。维护者使用可信的正规供应商，并认为 traceback 对模型修正工具有实际价值，因此当前保留这一行为，不把内部路径随请求发送本身列为待修缺陷。普通工具异常只回传类型和错误文本。密钥、聊天正文或额外运行数据仍不应被无意加入异常文本。

值得保留的是：普通函数、签名和 docstring 组成一个可直接复用的能力面。未来若调整边界，应优先修改入口、启用范围或少量检查，而不是仅因工具锋利就建立庞大的工具对象宇宙。
