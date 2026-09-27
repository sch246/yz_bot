# LLM 聊天、上下文与工具调用

本文描述当前源码中的 LLM 主路径。运行时供应商、模型、价格和提示词来自 storage，本页不复制设备上的真实配置、聊天内容、账号或密钥。工具模块以 `mods.tools` 的进程内 last-good registry 为权威，当前任务能调用哪些函数则由该 `Chat` 的模块绑定决定；模型是否真正收到工具，还取决于 `llm_system/config` 中对应模型的 `function_calling` 标记。

## 入口与状态范围

主聊天入口是 `mods.chat.capture_chat`：`#` 本地子命令先就地执行，其余事件由 `activation_signal()` 判定是否唤醒唯一中心 reader。公开的 `cond()`／`call()` 只保留给可能从 `.py`、动态 link 或 `#hint` 引用它们的旧兼容代码，不再是正式路由的 `chatting` 入口。触发包括：

- @ Bot；
- 以“柚子，”开头的文本；
- 戳一戳 Bot；
- `#...` 控制命令。

群聊只有群号位于 `chat_groups` 时才进入这条路径；私聊没有这层群白名单。普通 `#` 命令先由本地子命令解析，返回的 callable 直接执行而不请求模型；`#poke` 和不能解析成控制命令的普通触发才会进入聊天。`.chat` 则建立一次单句请求。

群内其它成员之间的戳一戳不会单独触发 LLM，但可登记为群聊未读，之后经正式阅读成为中心已读事件。因此“能被模型看见”与“立即触发模型”是两个不同边界。

每次请求都会新建一个 `Chat` 对象，但共享进程级 `LLMClient`、供应商客户端和 storage。主聊天由唯一中心 reader 从全局已读信息流重建；`oplog` 是各来源窗口未读状态的唯一权威，chatlog 保存原始聊天档案。独立 `.chat` 仍是窗口内的单句请求。因此：

- 群聊和私聊消息在被中心 agent 实际阅读后进入同一条全局经历；
- 来源窗口和私聊身份始终保留在输入元数据里，`say` 必须明确目标；
- 主模型、prompt、图片档位、工具状态和预算从全局 `agent` 设置读取；旧窗口设置原样保留，独立 `.chat` 仍可读取其窗口配置；
- 主模型运行时不把某个群友事件当作隐式当前窗口；窗口相关工具须显式给目标或不在中心会话中启用。

## 上下文如何组装

中心 reader 首次激活时，从 `AGENT_WINDOW` 的统一已读流按 `max_events` 与 `max_token` 选最新可见后缀，将起点持久保存为全局 `agent.history_start`；空历史也保存已选择状态，最新单条超限仍至少纳入该条，起点不拆开原生 O/R。之后每次激活（含重启后）从固定起点显示全部可见经历，不再按上限自动滑窗；覆盖仍隐藏原文，起点事件即使后来被覆盖也不重选。op 用 `#agent reset_start` 安排**下一次激活**重选，当前轮不变。默认上限为 500 条／40000 token；`#limit` 或 `#agent limit` 修改同一份全局设置，但单改上限不重选已有起点。旧窗口覆盖保留原数据，只供独立 `.chat` 读取。`聊天开始／结束`是普通内容。`get_msgs()` 保留为窗口级辅助查询，不是中心开局的读取入口。

中心 agent 可用 `cover_events(ids, conclusion)` 将已读正式号归入本次行动的结论，节点沿用输出号和行动位置。显式调用如 `cover_events(["20260923-4", "20260923-5"], "结论")`；范围调用如 `cover_events(ids=[], conclusion="结论", anchor="20260923-4", before=2, after=3, kinds="input")`，或改用 `start`／`end`。`ids=[]` 是当前工具 schema 为保留旧位置参数而要求的范围写法。覆盖范围没有条数上限；成员可跨来源窗口，也可重复被不同节点覆盖。首次覆盖仍须在当前已读可见流中，旧号经明确 `recall_events` 且整段结果进入模型后也可点名。输出与整批返回保持强绑定，say 回声可单独覆盖，冻结的 `<sent_by>` 引用仍保留：筛选只选种子，实际闭包可越过范围、种类和来源；任一最终成员不可覆盖则整次失败，不自动补入未读成员或静默删成员。覆盖只改变默认投影，不删原文或原号；`recall_events` 可按原号反查实际冻结成员。独立 `.chat` 和子代理不能提交中心覆盖。

覆盖成员允许多重归属：同一正式事件可成为多个结论节点的成员。`_covered` 只是默认隐藏并集，节点各自冻结实际成员；重复引用不解除隐藏。中心 agent 可跨窗口引用旧号，但私有 `.chat` 仍只访问自身窗口。强绑定闭包和未完成行动检查不因跨窗口而放松。

`oplog` 从已登记输入的模型可见文本、输出正文与行动参数、返回参数与正文中识别当时已存在的正式号；中心输出允许指向旧窗口号，私有记录仍受窗口限制。`event_links(ids)` 或 `event_links(anchor="20260923-4", before=2, after=2, kinds="result")` 可查看一跳文本引用、被引用者、覆盖成员／所属节点、结果来源及输出 `reads`（从各 input 的 `read_by` 派生的实际读入号，不按输出后位置猜测）。筛选只限制查询根，邻接边完整返回；它不读取正文，也不授予覆盖信用。覆盖、文本出现编号与某结论的真实依据是不同关系；搜索树可沿节点 `Y` 的覆盖成员找到返回 `R`，再沿 `R` 的文本引用找到旧记录 `E`。这里尚不实现自动退休、高度计算或语义证据核验；私有窗口的邻居可见性暂不随选择器改变。

`recall_events`、`event_links`、`cover_events` 共用正式事件选择：显式 `ids`、`anchor` 加前后数量，或同时给 `start`／`end`，三者只能选一种。范围沿 `oplog._events` 的唯一已读追加顺序含端点解析出实际 IDs，不设条数上限；`kinds` 和 `source` 随后才过滤，不重排，也不按筛选命中补足。缺选择器、混用、不完整区间、非法种类或来源都会报错。`recall_events(start="20260923-4", end="20260923-8")` 直接返回解析后的完整事件及冻结的 `resolved_ids`，含已覆盖或旧版已收缩的可读事件；它不进入未读信源、不按字符分页，也不拆成多条模型消息。`take` 消耗选中的未读成员，并给世界事件正式 input 号；反查则由当前输出 `O` 发起行动，旧内容随新的工具 result 事件 `R` 入流，`R` 的文本引用旧事件 `E`（`O → R → E`）。旧 `E` 不移动、不复制、不重新编号，也不消耗未读成员。通常总结这次探索时覆盖 recall 的 `O/R`，结论再引用旧号，形成“结论 → 回看结果 → 旧事件”；完整回看后也允许直接覆盖旧 `E`，但那是另一次明确选择。覆盖可见性信用只在整个工具结果实际进入模型后按返回的 `resolved_ids` 授予，不在工具执行时提前授予。选择器不扩展到聊天档案或未读信源。

一次模型输出的全部同步工具返回完成时立即登记一个普通 result R；正常下一子请求先追加当前会话暂存的完整 R 投影，再追加通知和显式阅读，不按同批工具数量均分、二次截断或自动分页。`take`、`pull`、`mentions` 与 `read_messages` 的 R 只确认安排、不带选中消息正文；同批安排的全部阅读在下一 provider 请求前按顺序逐条全文登记为正式 input，不因 token 预算截断、分批或报错。没有下一请求，安排不消费。每条新 input 保存并投影发起输出号 `read_by` 和实际公开工具名 `read_via`；同一输出的多个读取行动共用输出号，不用 `#位置` 作读取身份。跨过已读/跳过成员时，桥附属于新 input 的持久投影；已读桥保留旧正式号与冻结的完整投影，跳过桥没有旧 input 号并显示 `skipped_by`/`skipped_via=mark_read`。连续桥超过 5 条仅展示首尾及中间折叠数。旧 input 没有 provenance 字段时留空；旧 mark_read 跳过桥没有发起输出号时显示 `skipped_by=unknown`，不伪装成未读。`recall_events` 仍同步返回完整旧经历，同一输出里的工具彼此看不到结果。若 `say(final_call=true)` 结束、请求取消或失败，不为结果强迫续轮，也不做交付确认；下次激活从固定历史起点重建，已覆盖或在起点之前的内容需要时可用 `recall_events` 主动反查。极窄取消窗口可能让模型没读到已经登记的 R。`mark_read` 保存发起输出号并跳过当前成员，不产生 input；之后仍可用 `read_messages` 再次阅读 chatlog。

中心 reader 每次请求前估算实际送达的已编号事件文本成本；超过全局 `pressure_percent`（默认 75%，由 `#agent limit` 的第三项设置）时，在模型可见末尾 hint 显示“上下文占用 {已用}/{上限} token”。达到 `max_token` 时改为明确要求先用 `cover_events` 压缩已读经历、再做别的事；这只是提示，不拦截工具，也不自动裁剪历史。它不计事件条数，不是聊天结束后向 QQ 发状态的 `#hint`；这里的 token 是本地文本估算，不等于 API 实际使用量。

中心 agent 可调用 `edit_hint(text)` 整体替换全局待办，空字符串清空；文本以 `agent_hint` 存在全局 `agent` storage。每次模型子请求从该值重新生成末尾 hint，历史中不追加旧版文本（工具行动本身仍留痕），也不会发 QQ 消息。同一模型输出中多个工具调用的参数已一起生成，彼此看不到结果；不能在同批 `edit_hint` 中宣称前面的 `cover_events` 已成功，须等返回进入下一子请求再更新依赖结果的待办。动态未读概况和压力提示是另外两段可重算 hint；向 QQ 发结束状态的 `#hint` 命令仍按来源窗口配置。

构建时会：

1. 跳过以 `#` 开头的本地控制消息和无关 notice。`#` 是一条跨模块约定：LLM 失败信息（`llm.Chat.chat`）、`.py` 与 link action 的 traceback、`#` 子命令的输出都以它开头，`chat.get_msgs` 据此把它们排除，使调试输出不回流进模型——它们占 token，还会让模型看到自己的错误堆栈。过滤对所有发送者一视同仁，Bot 自己发的与用户发的 `#help` 一样不进上下文。改任何一个生产端的前缀都会让那类输出开始回流，且不会报错；
2. 将“聊天开始”或“聊天结束”作为普通消息，不改变回看范围；
3. 用 `msg2chat()` 将普通 OneBot 事件投影为 `role=user`，显示来源窗口、作者、名字、时间、消息 ID 和可用的档案位置；缺失时间标为未知，不猜。Bot 的 `say` 动作与随后 QQ 回声是两次经历，按唯一 `message_id` 确认关联，不伪装成同一次输出；
4. 群聊中所有戳一戳、私聊中只有戳 Bot 的事件，会复用 chatlog 的姓名/群名片格式生成 `role=user` 的本地事件文本；
5. 普通消息和戳一戳事件在首次选定历史起点时共同参与 `max_events`／`max_token` 选择；之后不因超限自动裁剪。

`init_chat()` 装配主设置选定的提示词、base、中心 agent 身份说明与从固定起点开始的全局已读流；`_activate_chat()` 从全局 `agent.active_tools` 恢复实际加载状态。工具说明在请求前进入正式 input，不放开头。`recall_events` 同步返回正文；`take`、`mentions`、`read_messages` 只在下一安全子请求边界追加正式 input，通知不含未读正文。

中心主聊天的图片档位由 `#agent image <mode>` 全局设置；旧窗口 `#image` 仍可用于独立窗口会话。名称与数字别名分别为 `off/0`、`lazy/1`、`eager/2`；历史布尔值兼容为 `False → off`、`True → lazy`。`off` 会把历史消息中的图片 part 降级为 `[图片(URI)]` 文本：避免把 `image_url` 发给纯文本模型。`recognize_image` 对独立 `.chat` 或显式加载 image 模块的会话仍可用；中心 agent 当前隐藏整个 image 模块，因为生图函数仍依赖隐式当前窗口，不能说中心模型可按需调用识别工具。`lazy` 只在 LLM 聊天实际触发时处理本轮上下文中的图片；**一次对话内同一张图只解析一次**——这里的“一次对话”指 `chat.chat()` 的一次持有（多轮工具调用与插话续写都算同一轮，直到它的 `finally`），解析失败的不再逐轮重试（腾讯 `rkey` 过期后只会拿到 HTML），成功的也直接复用，对话结束后重新聊会重新检查。这项检查台账挂在线程局部，eager 预取与 `.chat` 单句各记各的。`eager` 在图片消息到达时立即启动后台下载：中心聊天模型能直接读图时只缓存原图，纯文本模型才会同时预生成文字描述。同一来源若恰好同时被 eager 和聊天请求，描述生成会等待同一个进行中任务，避免重复计费。纯文本模型的识别结果统一压成一个 `[图片(URI)识别结果：描述]` 文本 part，明确 URL 与描述属于同一张图片；视觉模型收到的实际图片 part 前会保留一段“下方图片的原始链接”文本。图片加载失败或没有可用视觉模型时也使用同一个带 URI 的单段格式。

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

当前窗口可以用 `#model` 和 `#models` 查看当前选择及其模型列表。`#use_model <provider>/<model>` 会保存一个完整模型选择，并且只以参数中第一个 `/` 为分隔，所以模型名本身可以继续包含 `/`；不带参数的 `#use_model` 重置该选择。只有**供应商**必须已配置：没有 base URL 与 API key 就不存在可调用的对象，命令会拒绝它。**模型不必登记在配置里**：未登记的 `provider/model` 原样下传，本地按「支持视觉与函数调用、价格 0」处理（`models.UNKNOWN_MODEL_CAPABILITIES`），模型是否真的存在由对端回答。所以打错的模型名拿到的是一条供应商报错，而不是本地的拒绝。`#models` 先向对端请求它当前提供的模型列表，取不到（网络、密钥或对端不实现 `/models`）就退回本地配置并在末尾说明已回退；本地没有元数据的行只显示名字（价格为 `-`），能力标记留空；价格列读的是「输入未命中 / 输入命中 / 输出」，显示按当前时刻调用 `price_fn` 后的单价。这些取舍见[模型选择的宽松解析](working/proposals/model-selection.md)。源码内的默认配置只用于首次创建空配置，不能代表当前设备正在使用的服务。

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

每个 `Chat` 开局默认激活标准 Python 模块 `meta.py`。中心 agent 的完整模块说明作为正式 input 进入经历流，子代理在开头显示；导出的工具一律不带前缀，其中四个是**必需**的恢复入口（少一个模型就没法自救）：

| 工具 | 当前效果与边界 |
|---|---|
| `exec_code` | 在 `.py` 的共享 `loc` 中先 `exec(code)`、再 `eval(expr)`；`timeout` 必填（秒，`0` 表示不限），代码跑在一个可终止的子线程里，到点终止它启动的子进程并中断该线程。调用时仍检查 op，拥有与 `.py` 接近的进程和宿主机能力。 |
| `list_tools` | 列出 last-good 模块、当前会话激活状态、空闲回收规则、最近加载失败及磁盘差异。 |
| `reload_tools` | 按模块名从磁盘显式应用、更新或删除模块；逐项返回成功和带完整 traceback 的失败结果，失败保留旧 last-good。 |
| `load_tools` | 将 last-good 模块激活到当前 Chat；主会话名单存全局 `agent`，独立 `.chat` 保留窗口名单；不读取磁盘。 |

仓库当前提供以下按需模块；函数只在对应模块激活后出现：

| 模块 | 导出能力 |
|---|---|
| `common` | `common__get_time`、`common__poke` |
| `image` | `image__recognize_image`、`image__create_image`、`image__create_image_from_references` |
| `later` | `later__later_add`、`later__later_del` |
| `user_data` | `user_data__get_user_data`、`user_data__set_user_data` |
| `agents` | `agents__assign_tasks` |
| `host` | `host__read_file`、`host__write_file`、`host__run_command` |
| `minecraft` | `minecraft__search_mc_mod`、`minecraft__check_mod` |
| `weather` | `weather__search_city`、`weather__get_realtime_weather`、`weather__get_daily_forecast`、`weather__get_hourly_forecast` |

`host` 是 `exec_code` 之外的常用出口：文件读写和 shell 都不必再绕一次任意代码执行。读取按本次实际截取的窗口计字节，超过 `max_bytes` 时只回大小和后续建议（缩小区间、先 `grep -n` 定位、或用 `assign_tasks` 开子会话通读后带回摘要），不做分页会话。改写靠 `read_file` 首行 header 里的 `line`/`size`/`v` 定位与对账：位置与新内容的写法无关，`v` 只在该区间自读取后被改动过时失配，失配的返回自带现状和新 header。渲染格式与 `.edit` 同源，所以手动编辑和模型改写看到的是同一种东西；它没有目录白名单，`data/`、`config.json`、`.env` 和聊天记录都在可及范围内。

除 `weather` 继续投影可用的 `mods.weather` 函数（其 schema 描述来自 `mods.weather` 函数自身的 docstring，与命令的 `-h` 帮助同源）外，这些文件持有各自工具的真实实现，不再从 `mods.chat` re-export。图片和子任务模块只惰性复用 `mods.chat` 的 usage/cost 入口，计费状态仍只有一份。

历史源码中有实现、但 `add_tool` 注册被明确注释的 `read_data`、群成员、农历/小六壬、跨窗口发送、`later_list/later_set`、URL 转 CQ 和百科工具，只在 `mods/tools/disable/README.md` 留有决策记录：说明它们当时是什么、现在等价能力在哪里、以及 RAG 等只剩草稿或死名字的项为什么不复活。仓库不保存永不运行的实现；要启用就按现有模块格式在 `mods/tools/` 顶层重写。

`create_image` 使用 OpenAI 原生 `gpt-image-2` 的参数与返回形状，不传 DALL·E 的 `style`、`standard` 或 `response_format=url`。图片以 `b64_json` 返回，解码后使用 `data/tmp_files` 的现有临时图片缓存和过期清理机制；Base64 本身不会进入 QQ 消息或 chatlog。

`create_image_from_references` 的 `image_uris` 参数每行接收一个 URI，并在输入边界按完整 URI 去重。解析后复用统一的内容寻址文件缓存；`file://` 必须是本机绝对路径。所有参考图都需通过图片格式和 20 MiB 上限校验。`gpt-image-2` 会自动高保真处理参考图，请求不传 `input_fidelity`。供应商是否对参考图输入另行计费尚未实测；当前 usage 仍只按实际返回图片数以每张 0.13 元记录。

## 统一模块格式与两种加载

`mods/tools/` 只扫描顶层、不以下划线开头的 `*.py` 和 `*.md`。同 stem 的 Python 与 Markdown 文件冲突；子目录不递归扫描，可以由顶层内容引用或由 Python 正常 import——这是有意的分层而非遗漏：模块目录是常驻上下文，递归会让子文件夹的内容一开局就全部占位，只列顶层则把它们降为"展开之后按需索引"的一级，与首行/全文的分层同理。两种文件共享以下最小格式：第一行是总会出现在模块目录中的描述，后续全部是激活后内容，不设 front matter、summary 或另一套 skill 协议。Markdown 到此结束；Python 还必须显式声明 `__all__`，其中可列零个或多个同步函数。

`mods/tools/__init__.py` 是 loader/registry 包，不是工具格式。`meta.py` 才是已经激活的基础模块：它和其它 Python 模块一样使用第一行 summary、后续说明、普通函数与 `__all__`，但作为必需恢复入口从开局就激活全文，四个函数名不加 `meta__` 前缀。其它顶层模块仍只默认显示第一行，显式激活后才加入余下内容。`meta` 必须**至少**导出那四个恢复函数（`exec_code`/`list_tools`/`reload_tools`/`load_tools`），并同时承载始终可用的聊天、信源和记忆工具；校验只读「少了哪个」（`_BASE_TOOL_NAMES` 与 `absent` 判定），不冻结额外导出数量。原先要求 `__all__` 与那四个完全相等是迁移带来的附带收紧；基础模块还要增加其它始终可用能力，精确相等会让整个恢复入口加载失败。删除其磁盘文件后 reload 会失败并继续保留旧 last-good，而不是卸掉恢复入口。

模块可以在顶层写 `BOT_OP_ONLY = True`，声明"只有 Bot 自身拥有 op 权限时可见、可加载"（目前只有 `op.py` 这么做）。Bot 的权限来自 `config.bot_permissions.op`，在一次进程生命周期内固定，不读取 `context.current()`，因此不会因这一轮读到谁的消息而出现或消失。三层都拦：渲染目录时过滤（`tools.bot_op_tool_visible`）、`SessionBinding.load` 拒绝（模型记得名字直接 load 也没用）、工具执行时再查一次 `op.bot_is_op()`。

中心 reader 的模型与辅助调用记入 Bot 的全局月账，不随唤醒作者或 `context.current()` 改变；独立 `.chat` 仍记发起者，旧月份不迁移。`cmds__run_command` 留空 `sender` 时以 Bot 自身执行，显式代行人类 op 时 Bot 自身也必须有 op 权限；下游仍按被代行者判权。`message.recvmsg(sender_id=X)` 只在可编程环境中使用，不直接暴露为模型工具。

Python 候选作为正常模块执行，可以 import 第三方依赖、其它 `mods` 和同目录下划线 helper。每个导出函数都必须有可用的签名、参数类型标注和 docstring，并通过现有 `Tool` schema 校验；模块内任一导出失败，整个模块都不替换。加载候选不会调用导出函数，但会执行顶层 import 和其它顶层语句，所以这里与 `.py`、命令、link 和宿主机操作属于同一信任域，不是沙箱；顶层应只放 import、常量和定义。

首次创建模块目录提示时，registry 会独立尝试每个文件，成功者成为进程级 last-good，失败者记录 traceback 而不阻断其它模块或 Bot。运行中有两种不同操作：

- `reload_tools(names)` 读取磁盘并逐模块应用变化。成功才原子替换 last-good；失败保留旧版。源文件已删除时，显式 reload 同名模块才删除 last-good。
- `load_tools(names)` 完全不读磁盘，只把 last-good 模块激活到当前 Chat；中心实际加载名单写全局 `agent.active_tools`。曾发送过的函数定义即使卸载仍保留，但调用只返回重装提示。

因此改文件本身不生效；末尾 drift hint 只报告磁盘差异，不自动加载。`list_tools` 在调用时报告 last-good、当前激活状态和磁盘差异；没有 watcher。Markdown 与 Python 服从同一手动应用生命周期。成功 reload 替换进程级 last-good；中心会话在下一请求边界同步共享 registry，再登记变化。

### 激活属于会话主体

中心 agent 的实际加载集合保存在全局 `agent.active_tools`，每个模块保存最后装入或调用的时刻；`SessionBinding.restore` 在新一轮开局按它恢复，闲置超过 1 小时的模块不再激活。`touch` 在工具结果后立即写回使用时刻。权限暂时不足的模块保留原时刻在激活名单里，恢复权限后仍可装回；源码已删除的模块才从名单删除。子代理不继承中心激活名单。

函数定义的生命周期与激活分开：`agent.tool_schema_modules` 保存模块首次加载的顺序；卸载后定义仍在请求中，但调用只返回先 `load_tools` 的提示。重启从当前 last-good 代码恢复定义，不保存旧 schema。调用同一请求里的函数始终使用请求开始时冻结的 callable；共享 registry 经其他会话 reload 后，中心在下一请求边界同步本会话函数再告知变化。`list_tools` 返回完整当前状态和磁盘差异。

### 追加式上下文

中心请求前的唯一正式工具状态写入点是 `chat.agent._agent_provider`。它先交付持久 R、通知与正式阅读，再同步共享 registry 与本会话函数，最后把完整或差异工具状态登记为有正式号的 input。写入成功后才更新全局“已告知”；工具执行途中不插入状态。子代理的状态只在开头显示，其后变化由本会话 provider 追加，不写中心 oplog。

如果正式上下文最终以 assistant 结尾，临时续接仅作为末尾 hint 进入本次发给供应商的消息，不进入 `Chat.messages` 或 oplog；先追加正式状态再判断，避免下一次重建时旧前缀被改写。磁盘源码差异也是末尾 hint，只报告、不加载。模块正文内的 `</system-reminder>` 会转义，避免逃出系统框架。

### 末尾 hint

hint 与追加式上下文是两层，判据是一句话：**频繁变化、且随时可以重算的状态放 hint；"发生过一次"的事实放 provider。**

- provider 追加进 `Chat.messages`，进历史、可回放、会一直留着。
- hint 每次模型子请求重新渲染一次，只挂在**发出去的那一份**末尾，从不写回 `messages`。旧的自然消失，上下文里不会堆出几代互相矛盾的副本。
- 与 system 提示词一样支持用函数生成（`Chat.add_hint` 接受字符串或可调用对象），只是重置时机不同：system 在建会话时定一次，hint 每个子请求重来一次。
- 因此 hint 里只放随时可重算的东西，不放"只此一次、错过就没有"的信息——那种必须走 provider。

中心待办使用 `edit_hint` 保存为可重算的全局末尾 hint，而不是逐版挂到模型末尾；完成后整体更新或清空。长期可复用经验可写成 `mods/tools/*.md` Skill，经 `reload_tools` 应用、`load_tools` 按需插入。Skill 不代替原话索引：结论保留正式事件号，原话由 `recall_events` 反查。

`SessionBinding._drift_hint` 报告 `mods/tools` 磁盘源与 last-good 不一致的模块名；只报告不加载，也不重复工具正文。它每次请求重读磁盘，文件改回去，提醒就自行消失。


### 中心工具状态经历

中心上下文开头不再放自动变化的工具目录。首次请求和手动重选历史起点后的首次请求，系统把完整目录及已加载模块说明书（含 `meta`）登记为一个正式 input；后续每次请求前，在整批 R、通知和安排的阅读之后，对实际工具状态与全局 `agent.tool_state_told` 对账，只把变化追加为 input。该 input 的 `event.type` 为 `tools`，不占用 oplog 顶层信源 `source`，也没有未读 `arrival`。状态事件被覆盖只改变可见历史，不改变 `active_tools` 或重新触发全量告知。写事件成功后才更新已告知状态；两步之间崩溃可能重复一次告知。

`active_tools` 只保存实际加载模块和最后使用时刻；工具调用会立即刷新并写盘。`agent.tool_schema_modules` 另存模型曾获函数定义的模块顺序：`meta` 固定最先，其它模块首次加载后追加；空闲卸载不删除定义，但下一请求中的对应函数会只返回“先 `load_tools`”，不执行已卸载代码。同批工具调用仍使用发请求时冻结的 callable。重启后按保存的模块顺序和**当前代码**重建定义，不保存旧 schema 快照；代码修改、权限变化和手动重选起点可改变列表，其他时候只向尾部追加。`list_tools` 可随时取完整最新状态，末尾磁盘漂移 hint 仍只报告差异、不自动加载。子代理保留一次性开头工具状态，不写中心经历。

函数定义是否位于供应商缓存前缀、首次增添模块会损失多少命中，仍需部署后用 usage 的缓存命中数实测；本地假供应商无法证明这一点。

### 中心设置与旧窗口配置

`image`（图片档位）、`reasoning`、`tools`、`max_events`、`max_token`、`pressure_percent` 共用 `mods/chat.WINDOW_SETTINGS` 的归一化规则。中心主会话读全局 `agent` storage，op 用 `#agent` 修改整组设置，也可用 `#limit` 直接修改预算；旧窗口配置仍在原群／私聊 storage，不自动迁移，独立 `.chat` 仍可按窗口读取。

- **缺省值写死在代码里**（`DEFAULT_MAX_EVENTS` / `DEFAULT_MAX_TOKEN` 与各归一化函数的兜底分支），不再读 `llm_system/config.json`。中心运行期覆盖只有全局 `agent` storage 一份；`#limit` 与 `#agent limit` 是同一写入的两个命令入口。
- **事件数和 token 只选历史起点**：默认 `max_events = 500`、`max_token = 40000`。首次激活或 `#agent reset_start` 后的下一次激活用它们选起点；随后不再自动裁剪，超过 `max_token` 只提醒主动压缩。旧窗口覆盖不自动迁入全局主体。
- **`hint` 与 `prompt` 不在表里**：它们是复合值（dict / 列表），缺省来自别的存储，各自的合并只有一行（`{**default, **window}` 与 `data.get("prompt") or settings`）。塞进单值表反而要造间接层。
- 合并只在 `window_setting` 一处发生，没有别的间接层。

**旧档案主动翻阅。** 中心 agent 用 `read_messages(window, message_id|origin, before, after)` 在指定聊天窗口选择本地档案；QQ `message_id` 命中多条时须加 `timestamp` 或用稳定 `origin` 消歧义。选择先随工具批次取得 R，正文在下一子请求作为逐条正式 input；若命中 live/source 当前未读成员，登记该 input 时一并消费。已读档案可再次阅读，新的 archive input 保留来源元数据，但不充当 live say 回声。远端旧档先用 `fetch(g/u)` 固化成有名字的信源，模型不管理 NapCat 请求页。默认上下文不再为补满预算自动倒扫 chatlog。`#hint` 不触发旧档阅读。

### 操作历史与结论收缩

模型子请求的一次完整输出（思考、正文、零到多个行动）在派发工具之前取得一个全局 `YYYYMMDD-N` 号；行动以 `号#位置` 指名。QQ 输入和通知在阅读时、输出在派发工具前、同步结果在批次完成时进入同一追加式信息流；未读到达不预占正式号。来源窗口各自持有持久 pending；中心 reader 从有序成员中选择任意成员正式读取。到达与正式登记事实写入 `data/event_stream/YYYYMMDD.jsonl`，chatlog 仍是 QQ 消息正文权威。

- 中心上下文从首次选定的固定起点读取可见事件，编号输入、输出、结果按阅读顺序投影；开局只给不含正文的通知，`take` 选中成员在下一请求正式读取，`pull` 只作前缀别名，`mark_read` 只把调用时已有成员设为已读。DeepSeek 主模型的完整 O/R 批次保留原生 assistant 思考、正文、工具调用与 tool 返回，重启后从同一事件流重建；旧记录、换模型、行动不完整或配对字段不符时，整批用明确的已编号文本投影。原生工具 ID 只供供应商配对，不是稳定记忆号。`.chat` 与子代理仍用各自原生配对。
- 真实 live/source `message_sent` 在正式读入时，以 `message_id`、同目标窗口和唯一已登记的中心 say 返回匹配，匹配到的行动号当场冻结在 `<sent_by>`；档案重读、子代理发言及非唯一匹配留空，以后不回填。超时或缺少 `message_id` 不靠文本、时间猜。
- 新的中心 O 在原事件中保存供应商原生 assistant 字段及模型来源，包含思考全文、自言自语正文和有序工具调用；R 仍是一个正式事件，投影时可展开成多条原生 tool 消息。完整原生块末尾派生一条 `<event_refs>` user 标签，列 O、各行动的 `O#位置` 与工具名、R；无工具输出只列 O。标签不入日志、不取号，映射归 O，cover 时随原生块一起退出。首次选择历史起点时，标签 token 也计入估算、事件数仍按 O/R 计算，原生块超限可用有界文本投影选择起点；起点冻结后不再每轮按预算降级或裁剪，更不会留下孤立 tool 或标签。未完成的行动只显示已有事实，不补造结果。`cover_events` 把 O 与其全部 R 成组移出默认上下文；say 回声可独立覆盖，原文仍可反查。清理可见性不销毁日志、不复用号码，长期遗忘和物理退休另待裁定。旧 `condensed`/`clear` 日志只为重放既有生产数据保留只读兼容，不再有写入工具或命令。

- 流式结果中途截断不会登记半个输出或派发行动；输出写入日志失败则不派发。`oplog` 在写盘前校验可预见的拒绝条件；追加、fsync 或落盘后索引应用失败会进入失败态，后续写入和同批尚未执行的工具行动都停止，直到重启检查磁盘。行动开始后不会自动重放：重启恢复未读事实，而非重跑已登记的动作。

统一正式号表示中心信息流的登记顺序，不表示各 QQ 窗口的消息发生顺序；极窄的取消窗口里，已登记的同步 R 可能尚未送进模型。独立 `.chat` 和子代理可先在私有请求中读到原生工具返回，不替中心消费 `oplog` 未读；旧 per-window 正式号保留原身份，中心可按原号反查。

DeepSeek 在带 `tools` 的 thinking 请求中要求最后一条 `user` 后的每条 `assistant` 携带 `reasoning_content`；`chat.view._closing_hint` 在持久 provider 产物之后检查尾部，仅在需要时为 outgoing 附加临时 `user` hint，不写入 `Chat.messages`、oplog 或持久前缀，也不为供应商编造过往思考。此规则最小报文于 2026-09-17 验证；其他供应商不依赖该字段。

### 思考内容开关

主会话的思考载体模式由 `#agent reasoning` 全局设置；旧窗口 `#reasoning` 只影响独立窗口请求。`keep` 是默认值，`drop` 让中心已读输出使用不带思考的文本投影。

中心 agent 的 `keep` 将完整原生思考随 O 留到 cover 或手动重置历史起点，并在完整 O/R 时原生重建；`drop` 只将 O/R 作不带思考的已编号文本投影。两种模式都在同一 O 事件中留原文供按号反查。单句 `.chat` 和子代理的独立请求上下文仍使用原生工具配对，`drop` 在其工具循环内把思考字段换成空字符串。

中心模型跨轮从事件流重建，私有 `.chat` 的跨轮上下文仍从 chatlog 重建。


### 窗口的结束提示（`#hint`）

这是与上面[末尾 hint](#末尾-hint)一节不同的另一个「hint」，两者只是碰巧同名：那一节说的是**发给模型的那一份请求末尾**挂什么，这里说的是**用户存给某个聊天窗口的一段代码**，它会在这个窗口的聊天循环停下时被求值并发送。

命令面只做文本管理——写、看、开关，和 `#model`/`#prompt` 一系；「它在聊天结束时自动跑」是另一回事，两半分开看。

- **谁跑、什么时候跑。** 唯一中心 reader 在 `_drive_agent` 的 `finally` 求值一次，按最初触发窗口选择窗口 `#hint` 配置；这是向 QQ 发状态的旧机制，不是全局模型待办 hint。无 reader 所有权的早退与独立 `.chat` 不触发。
- **配置模型。** 两份字典——全局默认 `storage.get("", "hint")` 与窗口 `getchatstorage()["hint"]`——按显式顺序合并成 `{**default, **window}`，两侧都只认同名的 `code`/`on` 两个键，没有别的间接层。发不发 = 合并结果里 `code` 非空**且** `on` 为 `True`；`on` 缺失按 `False`。窗口可以只写 `on`（单独关掉默认提示，`code` 仍继承）。`#hint set <源码>` 写入 `{"code": …, "on": True}`，`#hint set` 无参清掉窗口配置、回落默认；`#hint` 与 `#hint default` 只翻开关，保源码。没有 `del`——源码是劳动成果，toggle 不该顺手删掉它。
- **求值。** 走 `py.eval_last(source, environment)`，与 link 共用同一份实现（原先 link 里那份私有副本已消掉）：前面各行 `exec`，末行 `eval`；末行为空、以 `#` 开头、或结果为 `None` 就不发。环境是共享动态环境 `py.loc` 的**一份副本**，另注入 `window`（本窗口的 `history.window` 键）、`usage`（最后一次中心请求的事件文本 token 估算）、`event_count`（该请求中不同正式事件号的数量）与 `context_limit`（该请求实际使用的事件数/token 上限）。后三项在中心 agent 仍活跃时冻结，避免结束后误读最初触发群的窗口设置。
- **输出带 `#` 前缀**（`"#" + cq.escape(str(result))`），因此不回流进模型上下文，也不自指。报错同样以 `#` 开头、附 traceback。
- **异常安全是硬要求。** 求值与发送的任何异常都在这一层吞掉、只写 `hint` 日志流，**绝不抛回 `finally`**：这段代码是用户自己写的、每次聊天都自动跑，让它抛出去就等于一段烂代码能污染聊天主流程的返回路径。（只吞 `Exception`——`SystemExit`/`KeyboardInterrupt` 是进程控制，不该被用户代码吃掉。）
- **「已用上下文」的出口是 `chat.context_usage()`**。中心 reader 记录最近一次子请求实际收到的已编号事件文本本地估算，包含当次通知或有界结果／mail 页，不把尚未正式读取的整个 pending 计入；不含 system 提示和工具 schema，也不是供应商返回的实际 token 数。
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

完整子响应先登记一个输出号、再派发工具；同步工具完成后登记一个整批结果 R。中心 DeepSeek 会话保留完整的原生 assistant/tool 配对，后续激活从固定历史起点重建；不完整批次与旧记录用文本投影。下一次请求再追加通知和显式 take／档案阅读 input。流式文本仍由回调逐段产出用于终端显示，不会分拆成多个输出事件；普通工具失败不会回滚已经发生的行动，`PersistenceError` 则立即停批而非变成工具结果。单句 `.chat` 不读取中心未读，继续通过原生配对获取同步返回。

若供应商返回 `reasoning_content`，流式路径会完整拼接该字段、非流式路径直接读取；中心 O 原样保存到事件文件，未覆盖且位于固定历史起点之后时可带回下一次 DeepSeek 请求。QQ 回复和 chatlog 不存这份思考。旧 O 只有存在标记，不能由系统补造思考。

循环当前没有最大工具轮数、总执行时限、确认步骤或副作用回滚。模型持续产生工具调用时会继续请求；外部 API、storage 写入、延时任务和代码执行都在工具被调用时立即发生。单次 HTTP 请求有超时（`llm_system/config` 的 `request_timeout`，默认 120 秒，流式响应每收到一个 chunk 重新计时），但整轮循环没有。

### 插话与 `^C` 打断

入站不被生成挡住：link 独立运行，消息照常在 `oplog` 登记为各窗口未读。过去每窗口各有 reader；现在全 Bot 只有一个中心 reader，另一次召唤加入通知，不并发生成。

中心 reader 登记在 `context.WindowTurn` 的 `AGENT_WINDOW` 键下；各来源窗口的有序未读成员只由 `oplog` 的 arrival、input 和 `mark_read` 事实决定，`context.Mailbox` 已删除。`context.window_lock` 串行化同窗口的 chatlog/history 写入、arrival 与正式消费；路由期间的事件对象→arrival 关联由 `context.remember_arrival`／`event_arrival`／`release_arrival` 临时保存，不写进事件 dict。通知递交、未读正文与正式 input 各有日志事实，不以一个红点代替；source 页内任意已读坐标从 input/source journal 派生。相关窗口的群友可用 `^C` 取消共享请求，未读与其它窗口的通知仍留在信息流里。

- **先接纳，后阅读。** 路由在同一窗口锁内写 chatlog/history 与 arrival；Bot 的 `message_sent` 回声也入列，但不再执行命令。中心开局只递交不含正文的通知，既不偷读末段，也不把未读整段追加。
- **未读集合与档案分开。** `take(source, start=1, count=8)` 按工具执行时当前未读的 1-based 序号选择连续范围；已读与跳过不计数，`ids`、`arrival`、`origin`、`message_id` 是高级精确入口；`mentions(source)` 选至多 500 条未读提及，`pull(source, count)` 是前缀薄别名。`mark_read(source)` 将调用时已有成员设为已读而不生成 input。`read_messages` 按窗口内 `message_id` 或 `origin` 选前后文；R 只确认安排，同一批的全部阅读在下一请求进入同一全文 input 路径，命中未读成员时一并消费。`recall_events` 仍直接同步返回 R，不进入未读集合、不分页。
- **召唤不是正文。** 新版通知每次只列上次确认后新来的唤醒，旧通知按原快照重建，不显示 QQ `message_id`；尾部 hint 给当前未读提及的时间与序号，take 执行时重新解析位置；模型输出持久后只确认这次叫醒已经递交，不改变未读集合。因而同一条召唤在 `take` 或 `mark_read` 前仍未读，但红点本身不反复启动模型；只有后来新到的召唤或模型已经显式安排的 take 会让中心 reader 继续，并只带上新唤醒。`final_call` 或取消后的 R 留在普通历史，下次激活从固定起点重建，普通未读本身也不单独开轮。
- **`#` 只参与控制，不进入模型正文。** 这类事件可留下到达和激活事实（`#poke` 会唤醒），但不会作为聊天正文投影；本地子命令仍就地执行。
- **收尾没有缝。** `context.finish_turn` 在全局 reader 登记锁内检查通知与显式阅读；新召唤要么被当前 reader 接住，要么在注销后领取新 reader。已完成而本轮不能继续的 R 保存在普通历史，下次激活从固定起点重建，需要全文可反查。
- **`^C` 打断。** `bot._route` 取消该交互线 waiter；若窗口属于中心请求的关联窗口，还取消 `AGENT_WINDOW` turn 并调用 `watchdog.stop(AGENT_WINDOW)`。LLM 在轮间与每个流式 chunk 后检查停止并关闭响应；已输出的文本不能收回，未完成的行动不会自动重放。
- **`^C` 也要能停住工具。** 上面那套是**软**停止：检查点全落在工具调用外面，而工具是同步执行的——2026-09-17 那次 `cwd="/"` 的 grep 卡死，`^C` 完全无效，只能 `.reboot`，卡住的那一轮还让整个窗口失能（后续消息只排队、不发言）。所以相关窗口的 `^C` 还会调用 `watchdog.stop(AGENT_WINDOW)`：把这次工具执行期间 spawn 的子进程 kill 掉（阻塞在 `wait`/`communicate` 上的调用因此返回），再给登记过的执行线程注入中断。工具循环也随之在每次调用**之前**检查一次 `should_stop`，于是这批并发调用里只要发现停止就整批放弃——已经补齐的 tool 消息留在那儿即可，这一轮的 `messages` 本来就随轮次结束丢弃。
- **登记表只在一次工具调用之内有效**（`mods/watchdog`）：`subprocess.Popen` 被包了一层，工具执行期间起的子进程自动进表，调用一结束就出表，所以工具起的常驻进程（REPL、MC 服务端）不会被后来的 `^C` 误杀。调用工具的那个线程**有意不登记**——半路掀翻正在写文件的工具，换来的不是"停住了"而是"半成品"。
- **剩下的够不到的地方**：`PyThreadState_SetAsyncExc` 只在目标线程回到 Python 字节码边界时才真正抛出，卡在 `time.sleep()` 或一个 kill 不掉的系统调用里时，注入是挂着的；另外卡在等待第一个 chunk 时仍然无法打断。`exec_code` 用另一招回避了这一点——它在**子线程**里跑代码，时限由调用方负责，所以无论代码卡在哪里，调用方都一定在 `timeout` 内拿回控制权，代价是那个线程可能还留着。

## `assign_tasks` 的子模型分派

`assign_tasks()` 是工具系统内再创建工具系统的高阶能力：父模型传入公共 prompt、多行 tasks、模块名列表、模型名和 `max_workers`，函数为每个 task 新建独立 `Chat`，在线程池中并发运行，最后按原任务顺序返回 `(task, result)`。

子会话和顶层聊天一样获得四个基础工具与模块目录提示，并预先激活父模型明确列出的 last-good 模块；它不自动继承父会话其它活动模块、聊天历史、base 提示或窗口提示词。子会话的模型使用父模型传入的 `provider/model` 字符串，不再另有固定 provider。模块列表可以包含 `agents`，但代码没有强制递归深度、并发上限或全局预算。

函数捕获原始消息，并在每个工作线程运行子 `Chat` 前安装为当前 context，结束时清除；依赖当前窗口的工具因此沿用父任务的聊天事件。子会话仍不拥有父会话的提示词和历史消息。

每次顶层 `_activate_chat()` 增加一次调用计数并恢复对应会话主体的持久工具名单；中心名单在全局 `agent`，私有 `.chat` 在窗口。子会话不经过顶层激活，响应 usage 仍会累加费用。中心主模型与辅助费用记到 Bot，独立 `.chat` 记发起者。当前费用只在供应商返回 usage 时按输入未命中／缓存命中／输出三段入账；供应商不返回 usage 时尚无发送 token 兜底。单价来自模型元数据，峰谷取请求发起时刻；缓存命中价单独计，两边都不报命中数时保守按未命中算。`.chattop` 按自然月记账，中心费用以 Bot QQ 号显示；“调用次数”并不等于所有底层 HTTP 子请求数。

## 当前信任边界与维护取舍

工具调用不是由可信 Python 调用者选择，而是由外部模型根据聊天上下文和提示词生成参数。模型发起的工具调用属于 Bot 自己的行动：需要宿主级权限的入口统一读取固定的 `config.bot_permissions.op`，不借最后一个触发者或当前读到的消息作者。`BOT_OP_ONLY` 模块（目前只有 op 工具集）也按同一声明决定目录可见性和加载；其它模块仍对所有 LLM 轮显示，但其中需要 op 的函数会在调用时检查 Bot 自身权限。没有逐工具确认。

这使以下能力处于同一条提示注入路径上：

- `exec_code` 可访问 `.py loc`，本质上接近任意代码执行；
- `host` 的三个函数可以读、改、删宿主机任意路径的文件并执行任意 shell 命令，与 `exec_code` 同级；读取会把文件内容发送给模型供应商，写入和命令都不可撤销；
- `reload_tools` 可以在进程内执行并应用 `mods/tools` 中的受信任 Python；候选顶层代码在校验期间就会执行；`load_tools` 可以把任意 last-good 模块交给当前模型；
- `set_user_data` 可读取模型生成的 Python 字面量并修改任意用户 storage；
- `get_user_data` 可把任意用户数据发送给模型供应商；
- 延时任务会在未来执行并回到当前窗口；模型工具创建和修改时按 Bot 自身权限判定，未来执行不重新换身份；人类直接使用 `.later` 仍按消息作者判定；
- `assign_tasks` 可以增加并发、费用和工具调用深度；
- `poke` 会立即对当前会话产生外部可见的戳一戳动作；
- `recognize_image` 可以下载模型指定的网络图片，或读取模型指定的本机 `file://` 绝对路径，再把图片及识别要求发送给视觉供应商，产生额外网络访问、宿主机文件读取和模型费用；
- `create_image` 会产生按张计费的外部 API 调用，并立即向当前 QQ 会话发送生成结果；
- `create_image_from_references` 还会下载模型指定的网络图片或读取本机 `file://` 绝对路径，并作为 multipart 文件上传给生图供应商；
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
