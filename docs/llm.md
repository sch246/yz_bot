# LLM 聊天、上下文与工具调用

本文描述当前源码中的 LLM 主路径。运行时供应商、模型、价格和提示词来自 storage，本页不复制设备上的真实配置、聊天内容、账号或密钥。工具模块以 `mods.tools` 的进程内 last-good registry 为权威，当前任务能调用哪些函数则由该 `Chat` 的模块绑定决定；模型是否真正收到工具，还取决于 `llm_system/config` 中对应模型的 `function_calling` 标记。

## 入口与状态范围

主聊天入口是 `mods.chat.capture_chat`：`#` 本地子命令先就地执行，其余事件由 `activation_signal()` 判定是否唤醒唯一中心 reader。公开的 `cond()`／`call()` 只保留给可能从 `.py`、动态 link 或 `#hint` 引用它们的旧兼容代码，不再是正式路由的 `chatting` 入口。触发包括：

- @ Bot；
- 以“柚子，”开头的文本；
- 戳一戳 Bot；
- `#...` 控制命令。

群聊只有群号位于 `chat_groups` 时才进入这条路径；私聊没有这层群白名单。普通 `#` 命令先由本地子命令解析，返回的 callable 直接执行而不请求模型；`#poke` 和不能解析成控制命令的普通触发才会进入聊天。`.chat` 命令已删除，`.chattop` 月账命令仍保留。

群内其它成员之间的戳一戳不会单独触发 LLM，但 chatlog 会记下它，之后出现在该群的窗口画面里。因此“能被模型看见”与“立即触发模型”是两个不同边界。

中心设置命令直接使用 `#` 前缀，没有 `#agent` 层：`#model`、`#models`、无参 `#prompt`、`#image`、`#reasoning`、`#limit`、`#reflect`、`#awaker` 和 `#help` 可查看；`#use_model`、带参数的 `#prompt`／`#image`／`#reasoning`／`#limit`／`#reflect`／`#awaker` 修改全局设置，要求 op。`#prompt [set <全文>]` 看或改写笔记 self；旧的 `#add_prompt`、`#setting`、`#use_setting`、`#set_setting`、`#del_setting` 已删除。`#hint` 不属于这些全局聊天设置：它由 op 管理，可覆盖本窗口或全局默认，并在最初触发窗口的聊天循环停下时向 QQ 发送状态。

每次中心请求都会新建一个 `Chat` 对象，但共享进程级 `LLMClient`、供应商客户端和 storage。中心 agent 在交接之前一直用同一段上下文，每次被叫醒只往后追加；交接后从笔记重新开始一段。chatlog 保存原始聊天档案，`oplog` 登记窗口到达、唤醒与模型的输出和结果。因此：

- 群聊和私聊消息都可以出现在同一个主体的画面里；来源窗口写在每段画面的标题上，`say` 必须明确目标；
- 主模型、prompt、图片档位、工具状态和预算只从全局 `agent` 设置读取；旧窗口聊天设置不再读取；每窗口 `#hint` 独立保留；
- 主模型运行时不把某个群友事件当作隐式当前窗口；窗口相关工具须显式给目标或不在中心会话中启用。

## 上下文如何组装

2026-10-22 起中心 agent 只要不交接，就一直在同一段上下文里（取舍见[无状态主循环提案](working/proposals/stateless-agent-loop.md) §7.2）。这一段存在 `data/context/current.json`（`chat.segment`），重启后接着用；系统提示每次重建，在交接之间不变，其余消息原样保存，包括原生 assistant/tool 配对与思考。

1. **新一段的开头**（交接后第一次被叫醒，`agent._head`），按顺序：
   - 系统提示：笔记 self、`_base_prompt`（含 `tools/memory_system.py` 开头"你的记忆怎么运作"那一节，每次从文件读）、中心身份说明；
   - 当前完整工具状态，这一段里不再重发，变化由 context provider 追加；
   - 笔记：global、recent（上次交接留下的）、最近一篇日记；
   - 目录（`roles.directory`），只列名字：其它最近有消息的窗口、笔记、日记。
2. **每次唤醒追加的材料**（`agent._run_agent`），前面的消息一个字都不动：
   - 这一段里第一次出现的窗口和作者（最多 6 人）的笔记（`agent._people`）；
   - 叫醒她的窗口画面：这一段里第一次出现的窗口给 `max_events` 条，已经出现过的从这一段里上次给到的那条记录之后接着给（记在这一段的 `last_shown`，不按看到时间，因为它只精确到秒）；交接后续做时沿用刚才看着的窗口；
   - "这次唤醒你的是（时刻）"、闹钟理由，或"你刚把上一段上下文交接掉了"。

   末尾 hint 是待办（笔记 todo）、`say` 提醒、其它窗口的未读数与当前时间，只挂在发出去的那一份上。请求失败、没有任何输出时这段文件不变，同样的材料不会追加两次；每轮结束后文件跟着更新。读回时，一批工具调用缺结果的（被打断）补一条"被打断"的结果；`#reasoning drop` 时以前几次唤醒的思考不再带回。
3. **窗口画面**（`chat.screen`）：直接读 `chatlog/` 普通日志文件里的记录，保留原文格式。
   - 每个窗口和日期补一行 `# g群号 YYYY-MM-DD`，`agent.seen` 记下的看到时间之后标出"以下是上次之后的新消息"。
   - 新消息超过 200 条时只展示最新的 200 条，画面开头写明跳过了多少条、用什么 `read_log` 参数往前翻，看到位置照样推进到最新。
   - 第一次输出时只把读画面那一刻之前到达的未读标为已读，生成期间新到的留给收尾接续。
   - 正文是 OneBot 原始串，图片 CQ 码按 `msg_split` 拆成图片 part，再按图片档位处理。
   - 名字是 chatlog 写入时的 QQ 侧名字，`.setname` 自定义称呼改为出现在这个人的笔记里。
   - sidecar 里的离线补回暂不渲染。
4. **交接**（`agent._handoff`）：驱动力是"这段上下文要被移除了，确认交接做好"。
   - 触发：她调用 `tidy(note)`；交接条件成立；夜里 awaker 的 `reflect` 规则到点。
   - 交接条件是 `data/notes/policy/tidy.py` 里的一段普通 Python，用 `.py` 的共享环境副本经 `py.eval_last` 求值，每次子请求结束（`stage="round"`）和每次唤醒往这一段追加之前（`stage="assemble"`）各一次，末行为真就交接，是字符串时就是原因。可用的名字有 `used`（这一段的 token 估算）、`grown`（这次唤醒长了多少）、`limit`（`#limit` 的整理阈值，`agent.tidy_token`）、`since_handoff`、`kind`、`hour`。文件不存在时用 `agent.DEFAULT_TIDY`：`used > limit`。写坏时按默认判断，终端留下 traceback。这一段里她还没有任何输出时不交接，免得开头材料本身就大时空转。她在反思里用 `edit_policy("tidy", …)` 改它，要 Bot 自身有 op 权限，写入前检查语法。
   - 做法：在同一段末尾追加交接指令（`roles.handoff_prompt`），用她平常的工具和同一段上下文跑一次，前缀缓存几乎全中。指令要她逐类确认：眼下的事在 recent，关于人和群的在 `person:`／`group:` 笔记，做法的教训在 global，答应的事在 todo，今天发生的事在 `diary:<日期>`（日期是此刻往前 6 小时的那天，改写成到目前为止的这一天，不往后追加）。每份先读，已经在了就不重写；`data/notes/policy/handoff.md` 是她在反思里给交接写的附加说明，附在指令后面。交接时 `say` 拒绝发言。
   - 交接完成后这一段连同交接的往来归档到 `data/context/archive/<开始时刻>.json`，`current.json` 删掉，下一次唤醒从新的一段开始；一次唤醒中途交接的，随即用新的一段接着做这次的事。交接出错、被打断或没有输出时不归档，这一段原样留着下次再交接；这次唤醒里不再重试。笔记都是改写成应有的样子、内容没变就不写，所以交接可以安全地重复。
5. **笔记**（`chat.notes`）：`data/notes/` 下的普通 Markdown 文件，改写和删除前把旧文件复制到 `.history/`，`notes.versions`／`read_version` 读回历史版本。
   - 范围：self、global、recent、todo、awaker、`person:<QQ号>`、`group:<群号>`、`diary:<YYYY|YYYY-MM|YYYY-MM-DD>`。
   - 她用 `write_note` 随时改除 self 以外的任何一份；改 awaker 时返回生效的规则数和不认识的行。
   - self 是 `data/notes/self.md`，她对自己的认识，取代了旧的提示词与命名设定：第一次读到时把当时生效的那份搬进来（系统消息拼成正文，示例对话写成“## 示例对话”一节），storage 里的旧值不删。她只能在夜间反思里用 `edit_policy("self", …)` 改；维护者直接编辑文件或用 `#prompt set`。
   - 待办以前存在 storage 的 `agent.agent_hint`，第一次读到时搬进 todo 笔记并删掉旧键。
6. **闹钟**（`chat.awaker`）：启动完成后在全局 APScheduler 上登记每分钟一次的 `chat-awaker` 检查，检查在自己的线程里跑，上一次没跑完就跳过这一分钟。
   - 规则读笔记 awaker，笔记为空时用 `daily 04:00 reflect`。
   - 每条规则上次响的时间记在 `agent.awaker_last`。
   - 间隔规则第一次见到时只记下当下；忙时也算响过。
   - 定点和一次性规则在到点后三小时内补一次；忙时留到下一分钟。
   - 理由是 `reflect` 时调 `agent.reflect`，否则调 `agent.alarm(理由, 规则)`。闹钟唤醒走 `_drive_agent`，没有窗口，理由追加在这一段末尾。
7. **夜间交接与反思**（`chat.agent.reflect`）：占住 `AGENT_WINDOW`。
    1. 先交接手上这一段（原因"夜里了"）。
    2. 若这天是月末，`roles.summarize` 把当月日记改写成 `diary:YYYY-MM`；年末再把月记改写成 `diary:YYYY`。
    3. 反思像人排查一样：材料是 global 与 recent 笔记、上次反思以来归档的段（段名、起止时刻、消息数、窗口）、`changes` 最近 40 行与 `proposals` 最近 20 行，以及一段说明：从症状出发，用 `read_context` 读当时那一段的原样，用 `read_log`／`search_log`／`read_actions`／`note_history` 查原始记录，找到原因再改。会话用新的一段，加载 `memory_system`，`say` 拒绝发言；材料之后新长出来的上下文超过整理阈值就结束；没有新归档的段时跳过模型调用。反思这一段也归档，名字带 `-reflect`。
    4. 结束后 `agent.pick_up_pending` 接上期间登记的召唤。

窗口第一次有输出时确认这次的通知，记下画面中每个窗口的看到时间，并调用 `reader.mark_window_read` 把未读登记标为已读。

`#` 是一条跨模块约定：LLM 失败信息（`llm.Chat.chat`）、`.py` 与 link action 的 traceback、`#` 子命令的输出都以它开头。画面渲染跳过正文以 `#` 开头的记录，使调试输出不回流进模型——它们占 token，还会让模型看到自己的错误堆栈。过滤对所有发送者一视同仁，Bot 自己发的与用户发的 `#help` 一样不进上下文。改任何一个生产端的前缀都会让那类输出开始回流，且不会报错。chatlog 里的通知行（戳一戳、撤回、管理变动等）原样出现在画面里。"聊天开始""聊天结束"是普通消息。

`init_chat()` 装配提示词与身份说明，`_activate_chat()` 计数并从全局 `agent.active_tools` 恢复实际加载状态。离线回放入口已删除，见[运行架构](architecture.md)。

中心主聊天的图片档位由 op 用 `#image <mode>` 全局设置。名称与数字别名分别为 `off/0`、`lazy/1`、`eager/2`；历史布尔值兼容为 `False → off`、`True → lazy`。`off` 会把历史消息中的图片 part 降级为 `[图片(URI)]` 文本：避免把 `image_url` 发给纯文本模型。中心 agent 可按需加载 `image` 模块：`recognize_image` 不依赖窗口，生图返回临时文件 URI，由模型再用带显式目标的 `say` 发送。`lazy` 只在 LLM 聊天实际触发时处理本轮上下文中的图片；**一次对话内同一张图只解析一次**——这里的“一次对话”指 `chat.chat()` 的一次持有（多轮工具调用与插话续写都算同一轮，直到它的 `finally`），解析失败的不再逐轮重试（腾讯 `rkey` 过期后只会拿到 HTML），成功的也直接复用，对话结束后重新聊会重新检查。这项检查台账挂在线程局部，eager 预取另在线程记账。`eager` 在图片消息到达时立即启动后台下载：中心聊天模型能直接读图时只缓存原图，纯文本模型才会同时预生成文字描述。同一来源若恰好同时被 eager 和聊天请求，描述生成会等待同一个进行中任务，避免重复计费。纯文本模型的识别结果统一压成一个 `[图片(URI)识别结果：描述]` 文本 part，明确 URL 与描述属于同一张图片；视觉模型收到的实际图片 part 前会保留一段“下方图片的原始链接”文本。图片加载失败或没有可用视觉模型时也使用同一个带 URI 的单段格式。

画面每次组装都重新读取，图片随请求按当前档位处理：纯文本模型的描述走描述缓存，缓存失效时会重新描述或给出失败占位；视觉模型直看图片时也可能因原图缓存过期而看到不同内容。`attach_image` 在不支持工具结果带图的模型上只把附图保留于当前子请求，不进 oplog。

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

每个 `Chat` 开局默认激活标准 Python 模块 `meta.py`。中心 agent 每次组装时在系统提示后给出完整模块说明，子代理在开头显示；导出的工具一律不带前缀，其中四个是**必需**的恢复入口（少一个模型就没法自救）：

| 工具 | 当前效果与边界 |
|---|---|
| `exec_code` | 在 `.py` 的共享 `loc` 中先 `exec(code)`、再 `eval(expr)`；`timeout` 必填（秒，`0` 表示不限），代码跑在一个可终止的子线程里，到点终止它启动的子进程并中断该线程。调用时仍检查 op，拥有与 `.py` 接近的进程和宿主机能力。 |
| `list_tools` | 列出 last-good 模块、当前会话激活状态、空闲回收规则、最近加载失败及磁盘差异。 |
| `reload_tools` | 按模块名从磁盘显式应用、更新或删除模块；逐项返回成功和带完整 traceback 的失败结果，失败保留旧 last-good。 |
| `load_tools` | 将 last-good 模块激活到当前 Chat；中心名单存全局 `agent`，不读取磁盘。 |

`meta` 还导出 `say`（唯一的发言方式，中心会话必须写明 `target`）、`read_log`／`search_log`／`read_actions`／`read_note`（召回画面以外的聊天记录、自己的行动记录与笔记）、`write_note`（改任何一份笔记，包括待办 todo 和闹钟 awaker）、`tidy`（这一轮结束后交接）和 `attach_image`。

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

`Chat.add_context_provider` 的产物在每次模型子请求前追加进 `Chat.messages`，前面的消息不改。中心会话用它追加一次唤醒内的工具状态变化：组装时先 `sync_registry` 并把完整状态（`state_text(None)`）放在系统提示之后，之后每次子请求前对比快照，只把变化追加。子代理的状态只在开头显示，其后变化由本会话 provider 追加。

磁盘源码差异是末尾 hint，只报告、不加载。模块正文内的 `</system-reminder>` 会转义，避免逃出系统框架。

### 末尾 hint

hint 与追加式上下文是两层，判据是一句话：**频繁变化、且随时可以重算的状态放 hint；"发生过一次"的事实放 provider。**

- provider 追加进 `Chat.messages`，进历史、可回放、会一直留着。
- hint 每次模型子请求重新渲染一次，只挂在**发出去的那一份**末尾，从不写回 `messages`。旧的自然消失，上下文里不会堆出几代互相矛盾的副本。
- 与 system 提示词一样支持用函数生成（`Chat.add_hint` 接受字符串或可调用对象），只是重置时机不同：system 在建会话时定一次，hint 每个子请求重来一次。
- 因此 hint 里只放随时可重算的东西，不放"只此一次、错过就没有"的信息——那种必须走 provider。

中心待办是笔记 todo，每次子请求都在末尾 hint 重新读一遍，而不是逐版挂到模型末尾；完成后整体更新或清空。长期可复用经验可写成 `mods/tools/*.md` Skill，经 `reload_tools` 应用、`load_tools` 按需插入。关于人、群和跨场合的事，她当场或在交接时写进笔记；原话不复制，需要时按窗口和时间用 `read_log` 读回。

`SessionBinding._drift_hint` 报告 `mods/tools` 磁盘源与 last-good 不一致的模块名；只报告不加载，也不重复工具正文。它每次请求重读磁盘，文件改回去，提醒就自行消失。


### 中心工具状态

每次组装都给出完整工具目录与已加载模块说明书（含 `meta`），放在系统提示之后；跨唤醒不变时这一段仍是稳定前缀。一次唤醒内的变化由 provider 追加（见上）。旧版写进 oplog 的工具状态 input 与 `agent.tool_state_told` 不再使用。

`active_tools` 只保存实际加载模块和最后使用时刻；工具调用会立即刷新并写盘。`agent.tool_schema_modules` 另存模型曾获函数定义的模块顺序：`meta` 固定最先，其它模块首次加载后追加；空闲卸载不删除定义，但下一请求中的对应函数会只返回"先 `load_tools`"，不执行已卸载代码。同批工具调用仍使用发请求时冻结的 callable。重启后按保存的模块顺序和**当前代码**重建定义，不保存旧 schema 快照。`list_tools` 可随时取完整最新状态。

函数定义与工具状态是否位于供应商缓存前缀、命中多少，仍需部署后用 usage 的缓存命中数实测。

### 中心设置与旧窗口配置

`image`（图片档位）、`reasoning`、`max_events`、`tidy_token` 共用 `mods.chat.AGENT_SETTINGS` 的全局归一化规则。中心主会话只读全局 `agent` storage；op 用 `#use_model`、`#image`、`#reasoning`、`#limit` 等命令修改它。旧窗口聊天配置不自动迁移，也不再读取；`#hint` 的窗口配置是独立例外。

- **缺省值写死在代码里**（`DEFAULT_MAX_EVENTS` / `DEFAULT_TIDY_TOKEN` 与各归一化函数的兜底分支），不再读 `llm_system/config.json`。中心运行期覆盖只有全局 `agent` storage 一份；`#limit` 是唯一预算命令入口。
- **两个预算**：`max_events`（默认 80，存储键沿用旧名）是窗口画面的默认条数；`tidy_token`（默认 30000）是整理阈值，即整理条件里的 `limit`：默认条件下这一段上下文超过它就交接；反思只限制材料之后新长出来的部分不超过它。2026-10-22 以前是 `max_token × pressure_percent` 两个数，第一次读到时折成 `tidy_token` 并删掉旧键。旧窗口覆盖不自动迁入全局主体。每晚反思的时刻在笔记 awaker 里，`agent.reflected_at` 记上次反思的时间。旧的 `agent.librarian_model`、`agent.tidied_at` 不再读取。
- **`hint` 与 `prompt` 不在表里**：窗口结束提示的 `#hint` 是 `data/hint/` 下的文件，独立合并全局默认与窗口覆盖；她对自己的认识是笔记 self（`data/notes/self.md`）。它们不是单值档位，不使用 `AGENT_SETTINGS` 的归一化。
- 已退役的窗口聊天设置仍可能留在生产 storage，但运行代码不再读取；其清理需先获维护者授权，见[第二轮调查](working/round2-legacy-and-backfill-audit.md)。

**召回画面以外的内容。** 中心 agent 用 `read_log(window, count, before)` 读任意窗口的 chatlog 原文（格式与画面相同，`before` 写 `YYYY-MM-DD HH:MM:SS` 或 Unix 秒，用来往前翻），`search_log(keyword, window, days)` 按子串在一个或所有窗口最近若干天（默认 7，最多 90）的聊天记录里找，最新的 30 条在前，`read_actions(ids, count, keyword, start)` 读自己的行动记录，`read_note(scope)` 读笔记。旧的 `take`、`status`、`mentions`、`fetch`、`read_messages`、`mark_read`、`pull` 已移除。`#hint` 不触发阅读。

### 行动记录

模型子请求的一次完整输出（正文、零到多个行动）在派发工具之前取得一个全局 `YYYYMMDD-N` 号；同步结果在批次完成时整批登记。这些记录写入 `data/event_stream/YYYYMMDD.jsonl`，chatlog 仍是 QQ 消息正文权威。新循环不把思考写进日志。

- 这一段上下文里她的输出和工具结果原样都在；交接之后要用 `read_actions` 按号或按词读回。
- `cover_events`、`recall_events`、`event_links` 已移除。旧日志里的覆盖、阅读 input 与 `condensed`/`clear` 记录仍按原样重放，新代码不再写入。
- 流式结果中途截断不会登记半个输出或派发行动；输出写入日志失败则不派发。`oplog` 在写盘前校验可预见的拒绝条件；追加、fsync 或落盘后索引应用失败会进入失败态，后续写入和同批尚未执行的工具行动都停止，直到重启检查磁盘。行动开始后不会自动重放。

DeepSeek 在带 `tools` 的 thinking 请求中要求最后一条 `user` 后的每条 `assistant` 携带 `reasoning_content`；每次唤醒追加的材料以 user 消息结尾，原生思考原样保存在这一段里（`#reasoning drop` 时以前几次唤醒的思考不再带回），所以旧的 `_closing_hint` 已删除。

### 思考内容开关

原生 assistant 消息（含 `reasoning_content`）随这一段上下文一起保存在 `data/context/current.json`，交接后随段归档；oplog 仍不写思考。`#reasoning keep`（默认）时以前几次唤醒的思考照样带回；`drop` 时每次唤醒读回这一段时去掉以前的思考，这次唤醒之内照旧保留（DeepSeek 带工具请求要求回传）。2026-10-19 的裁定是"思考只在一次唤醒之内保留"；改成同一段上下文之后，维护者 2026-10-23 定默认保持 `keep`。子代理的工具循环里 `drop` 仍会把思考字段换成空字符串。


### 窗口的结束提示（`#hint`）

这是与上面[末尾 hint](#末尾-hint)一节不同的另一个「hint」，两者只是碰巧同名：那一节说的是**发给模型的那一份请求末尾**挂什么，这里说的是**用户存给某个聊天窗口的一段代码**，它会在这个窗口的聊天循环停下时被求值并发送。

命令面只做文本管理——写、看、开关，和 `#model`/`#prompt` 一系；「它在聊天结束时自动跑」是另一回事，两半分开看。

- **谁跑、什么时候跑。** 唯一中心 reader 在 `_drive_agent` 的 `finally` 求值一次，按最初触发窗口选择窗口 `#hint` 配置；这是向 QQ 发状态的旧机制，不是全局模型待办 hint。无 reader 所有权的早退不触发。
- **配置模型。** 两份普通文件（2026-10-22 起）——全局默认 `data/hint/default.py` 与窗口 `data/hint/g<群号>.py`／`u<QQ号>.py`，首行 `# on` 或 `# off` 是开关，其余是代码；以前存在 storage 的配置在第一次读到时搬进文件并删掉旧键——读成字典后按显式顺序合并成 `{**default, **window}`，两侧都只认同名的 `code`/`on` 两个键，没有别的间接层。发不发 = 合并结果里 `code` 非空**且** `on` 为 `True`；`on` 缺失按 `False`。窗口可以只写 `on`（单独关掉默认提示，`code` 仍继承）。`#hint set <源码>` 写入 `{"code": …, "on": True}`，`#hint set` 无参清掉窗口配置、回落默认；`#hint` 与 `#hint default` 只翻开关，保源码。没有 `del`——源码是劳动成果，toggle 不该顺手删掉它。
- **求值。** 走 `py.eval_last(source, environment)`，与 link 共用同一份实现（原先 link 里那份私有副本已消掉）：前面各行 `exec`，末行 `eval`；末行为空、以 `#` 开头、或结果为 `None` 就不发。环境是共享动态环境 `py.loc` 的**一份副本**，另注入 `window`（本窗口的 `history.window` 键）、`usage`（这次唤醒最后一次子请求的上下文 token 估算）、`peak`（这次唤醒里最大的一次）与 `limit`（`#limit` 的整理阈值）。后三项在中心 agent 仍活跃时冻结，避免结束后误读不相关的窗口状态。
- **输出带 `#` 前缀**（`"#" + cq.escape(str(result))`），因此不回流进模型上下文，也不自指。报错同样以 `#` 开头、附 traceback。
- **异常安全是硬要求。** 求值与发送的任何异常都在这一层吞掉、只写 `hint` 日志流，**绝不抛回 `finally`**：这段代码是用户自己写的、每次聊天都自动跑，让它抛出去就等于一段烂代码能污染聊天主流程的返回路径。（只吞 `Exception`——`SystemExit`/`KeyboardInterrupt` 是进程控制，不该被用户代码吃掉。）
- **「已用上下文」的出口是 `chat.context_usage()`**。中心 agent 在每个工具批次结束时记录本次 `Chat.messages` 的本地文本估算（图片按占位计），包含系统提示与工具状态，不含工具 schema，也不是供应商返回的实际 token 数。`#hint` 的代码里可用 `usage`（这个估算的最后一次值）、`peak`（这次唤醒里最大的一次）与 `limit`（整理阈值）；旧的 `event_count`、`context_limit` 已删除。
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
  → 登记输出，执行行动并整批登记结果
  → 原生 assistant/tool 配对留在这一段上下文里，每轮后存盘
  → 这一段涨过阈值或她调用 tidy 时正常结束本轮，在同一段末尾交接、归档，用新的一段继续
  → 直到某轮不再调用工具
```

只有模型配置声明 `function_calling=true` 时，工具 schema 才会随请求发送。每次子请求发送前，循环从当前 `Chat.functions` 冻结一份快照；同一份快照同时用于请求 schema 和该响应返回的调用解析。因此 `load_tools` 或 `reload_tools` 在当前任务中执行后，新映射从下一个模型子请求起生效；已经发出的请求不变，同一模型响应中的其它调用仍使用原快照。

流式响应会逐段拼接工具名和 JSON arguments；当前轮模型流完整结束并产出所有普通文本后，才会把收集到的工具调用交给执行层。一次模型响应中的多个工具按收到顺序在当前线程同步执行，不使用事务。

工具返回值统一 `str(result)`；普通工具抛出异常时，类型和错误文本作为结果回传，完整 traceback 写应用日志；`reload_tools` 的校验失败则有意把完整 traceback 放进返回值，让模型能够修正模块源码。名字不在本轮快照里、或参数不是合法 JSON 的调用不再被丢掉，而是换成说明性结果（见上面「叫不到的名字不再吞掉调用」）。

完整子响应先登记一个输出号、再派发工具；同步工具完成后登记一个整批结果。原生 assistant/tool 配对只在本次唤醒里保留，之后以行动记录的可读形式出现。流式文本仍由回调逐段产出用于终端显示，不会分拆成多个输出事件；普通工具失败不会回滚已经发生的行动，`PersistenceError` 则立即停批而非变成工具结果。

若供应商返回 `reasoning_content`，流式路径会完整拼接该字段、非流式路径直接读取；它留在本次唤醒的消息里，不写进事件文件、QQ 回复或 chatlog。

循环当前没有最大工具轮数、总执行时限、确认步骤或副作用回滚。模型持续产生工具调用时会继续请求；外部 API、storage 写入、延时任务和代码执行都在工具被调用时立即发生。单次 HTTP 请求有超时（`llm_system/config` 的 `request_timeout`，默认 120 秒，流式响应每收到一个 chunk 重新计时），但整轮循环没有。

### 插话与 `^C` 打断

入站不被生成挡住：link 独立运行，消息照常写进 chatlog 并在 `oplog` 登记到达。全 Bot 只有一个中心 agent，另一次召唤加入通知，不并发生成。

中心 agent 登记在 `context.WindowTurn` 的 `AGENT_WINDOW` 键下。`context.window_lock` 串行化同窗口的 chatlog/history 写入与 arrival 登记；路由期间的事件对象→arrival 关联由 `context.remember_arrival`／`event_arrival`／`release_arrival` 临时保存，不写进事件 dict。相关窗口的群友可用 `^C` 取消共享请求。

- **先接纳，后展示。** 路由在同一窗口锁内写 chatlog/history 与 arrival；Bot 的 `message_sent` 回声也写进 chatlog，因此自己说过的话会出现在下次的画面里，但不再执行命令。
- **召唤不是全部。** 通知列出这次的唤醒（窗口、类型、作者、时间），它们决定这次画面里的窗口和作者笔记；一次唤醒里新到的消息不打断当前请求，只在末尾 hint 的未读数里出现，模型可用 `read_log` 去看，下一轮组装时进入画面。
- **`#` 只参与控制，不进入模型正文。** 这类事件可留下到达和激活事实（`#poke` 会唤醒），但不会作为聊天正文投影；本地子命令仍就地执行。
- **收尾没有缝。** `context.finish_turn` 在全局登记锁内检查是否还有未递交的唤醒；新召唤要么被当前 agent 接住，要么在注销后领取新的。整理之后不经过这一步，直接继续。
- **`^C` 打断。** `bot._route` 取消该交互线 waiter；若窗口属于中心请求的关联窗口，还取消 `AGENT_WINDOW` turn 并调用 `watchdog.stop(AGENT_WINDOW)`。LLM 在轮间与每个流式 chunk 后检查停止并关闭响应；已输出的文本不能收回，未完成的行动不会自动重放。
- **`^C` 也要能停住工具。** 上面那套是**软**停止：检查点全落在工具调用外面，而工具是同步执行的——2026-09-17 那次 `cwd="/"` 的 grep 卡死，`^C` 完全无效，只能 `.reboot`，卡住的那一轮还让整个窗口失能（后续消息只排队、不发言）。所以相关窗口的 `^C` 还会调用 `watchdog.stop(AGENT_WINDOW)`：把这次工具执行期间 spawn 的子进程 kill 掉（阻塞在 `wait`/`communicate` 上的调用因此返回），再给登记过的执行线程注入中断。工具循环也随之在每次调用**之前**检查一次 `should_stop`，于是这批并发调用里只要发现停止就整批放弃——已经补齐的 tool 消息留在那儿即可，这一轮的 `messages` 本来就随轮次结束丢弃。
- **登记表只在一次工具调用之内有效**（`mods/watchdog`）：`subprocess.Popen` 被包了一层，工具执行期间起的子进程自动进表，调用一结束就出表，所以工具起的常驻进程（REPL、MC 服务端）不会被后来的 `^C` 误杀。调用工具的那个线程**有意不登记**——半路掀翻正在写文件的工具，换来的不是"停住了"而是"半成品"。
- **剩下的够不到的地方**：`PyThreadState_SetAsyncExc` 只在目标线程回到 Python 字节码边界时才真正抛出，卡在 `time.sleep()` 或一个 kill 不掉的系统调用里时，注入是挂着的；另外卡在等待第一个 chunk 时仍然无法打断。`exec_code` 用另一招回避了这一点——它在**子线程**里跑代码，时限由调用方负责，所以无论代码卡在哪里，调用方都一定在 `timeout` 内拿回控制权，代价是那个线程可能还留着。

## `assign_tasks` 的子模型分派

`assign_tasks()` 是工具系统内再创建工具系统的高阶能力：父模型传入公共 prompt、多行 tasks、模块名列表、模型名和 `max_workers`，函数为每个 task 新建独立 `Chat`，在线程池中并发运行，最后按原任务顺序返回 `(task, result)`。

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
