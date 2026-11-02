# LLM 聊天、上下文与工具调用

本文描述当前源码中的 LLM 主路径。运行时供应商、模型、价格和提示词来自 storage，本页不复制设备上的真实配置、聊天内容、账号或密钥。工具模块以 `mods.tools` 的模块级 last-good 字典为权威，当前任务能调用哪些函数则由该次运行的独立 binding 决定；模型是否真正收到工具，还取决于 `llm_system/config` 中对应模型的 `function_calling` 标记。

## 入口与状态范围

主聊天入口是 `mods.chat.capture_chat`。`#` 子命令先就地执行；允许聊天的群和私聊按 `activation_signal()` 判断默认唤醒：@ Bot、名字开头、私聊对方发来的消息、戳 Bot，以及 `#poke`。群聊须在 `chat_groups` 内。其它成员互戳只留下记录；自己的 `message_sent` 回声只记录、不派发。

`chat.wake(event, kind)` 保留给消息派发中的 link、`.py` 和模块调用；不带事件时可随时调用 `chat.wake(reason=..., kind=...)`。非消息唤醒实际发生时，理由仍写入 `me` 的 chatlog，能用 `read_log("me")` 追溯。`chat.push(window, author, text, wake=...)` 先写窗口记录，再登记读状态与唤醒；公开签名保留。旧公开名的现场引用由[离线转换脚本](../scripts/once/README.md)定位。

窗口是 `(种类, 目标)`：QQ 群和私聊用 `g123`／`u456`，自己的只读窗口是 `me`，其它来源用 `种类:目标`。`chat.kind(name, send=...)` 登记发送函数；没有发送函数的种类只能看。`push` 可带来源的 `record_id`，否则生成编号。发送函数返回记录号表示已发出，`windows.Unconfirmed` 表示可能已发出，其它异常表示失败。

`attention` 统一待唤醒队列、认领与确认、覆盖层执行、等待、循环和防抖，一个计时线程等待最近期限。模型首次输出后才确认该请求展示的记录并 ack 召唤；请求失败释放认领，消息仍可再次叫醒她。主唤醒串行执行，生成期间的消息照常入库，相关窗口的新材料在下次子请求前追加。

默认触发器之上可设 `follow`（每条都叫）、`mute`（都不叫）和代码 `awaker.override` 的 `other` 函数。`me` 不能覆盖，master 的话不被 mute 压住；覆盖只决定默认唤醒，消息仍可未读，显式 `chat.wake` 与她设置的 `on/wait` 仍有效。`follow/mute/on` 以 awaker 文件为权威，跨重启保留；`other`、`wait` 和 loop 运行态只在内存。普通离线补回只补历史；离线时叫她的记录以身份登记，即使时间早于已确认位置也能被恢复和唤醒。

设置面使用 `#status`、`#model`、`#models`、`#prompt`、`#image`、`#audio`、`#reasoning`、`#effort`、`#temperature`、`#brake`、`#limit`、`#reflect`、`#years`、`#awaker`、`#debounce`、`#construct` 与 `#help`；修改全局设置要求 op。`#status` 合并显示待沉淀窗口、构造运行与循环运行态。模型设置、Bot 全局工具激活和预算属于全局 agent；每窗口 `#hint` 独立。精确语法以 `#help` 为准。

**B 站**（`mods/bili.py`，2026-10-23 起）：柚子自己的 B 站号，种类 `bili`。一个评论区是一个窗口：视频写 `bili:BV…`，动态、图文、专栏写 `bili:t<评论区类型>-<oid>`。每两分钟轮询一次"回复我的"和"@ 我的"，新的一条以评论号为记录号 `push` 进对应窗口并叫醒她，原因写"B 站有人回复你：《标题》"或"B 站有人 @ 你"；第一次轮询只记位置，不把账号以前的通知倒进来。每次只看第一页（约 20 条）；两分钟里来了一页以上，不往前翻、不全记在本地，而是在她的 `me` 窗口留一句并叫醒她，让她用 `bili__inbox` 去 B 站消息中心翻。通知里只有文字，表情是 `[doge]` 这样的文字，只有图片的那条正文换成一句占位；某一条数据不对就记日志跳过，写盘这类错误停在那一条、下次重试。她用 `type_word`／`send` 发评论，整段一条、不按空行拆，只能发文字（表情照 B 站写法写在正文里），正文开头 `[CQ:reply,id=评论号]` 表示回复那条，别的 CQ 码会被拒绝；评论工具把评论带的图片列成 `[图片] 地址`，她用 `attach_image` 看。楼中楼的楼主号由模块记着（通知、评论工具和她自己的回复都会记）。她发出去的评论只写进 chatlog、不登记到达（B 站没有回声）。刷首页、热门、搜索、视频详情、字幕原文、评论和楼中楼、评论点赞点踩与删除、视频点赞投币与收藏夹管理、个人主页与资料修改、动态查看与删除、关注与粉丝管理，以及直接读 B 站消息中心的 `inbox`（回复、@、赞、系统通知、私信会话，带未读数和翻页）和读私信的 `dm`，是工具模块 `bili`（`mods/tools/bili.py`），不进窗口；私信还不能回。登录是 `.bili login`，二维码只发到管理员私聊：管理员自己发，或者她用 `cmds__run_command(".bili login", target="u<管理员>")` 在管理员私聊里发起（谁扫码谁的号就成了她的号，所以不能发进群或发给别人）。私聊里只发二维码这一张图，扫码的说明由她自己说；登好了、过期了或出错写进她的 `me` 窗口并叫醒她；另一条路是她在自己的浏览器里登 B 站，用 `browser__get_cookies` 取出 Cookie（含 httpOnly 的 SESSDATA）交给 `bili__use_cookie`；两条路存凭证后都要用它查到自己的 uid 才算登好；凭证在 `data/bili/credential.json`，每天检查一次要不要刷新。

## 上下文如何组装

经历与眼前材料分别保存。`data/chat/journal/YYYYMMDD.jsonl` 是原始内容权威，保存稳定 `YYYYMMDD-N` 编号、时间、ctx、种类、原样模型消息和来源。每次运行有自己的编号视图，只保存有序条目引用与不能推导的统计（`stats.windows` 记行动和沉淀负责的窗口），结束时冻结到 `data/chat/views/<ctx>.json`。没有跨运行的当前视图，不再写 `view.json`；迁移脚本写出的那份（旧的当前段，没有经过交接）在第一次启动时由 `agent.adopt_last_view` 封存进 `views/` 和年鉴，并在 `me` 留一条叫醒她补记的记录。2026-11-01 起不再有整段压力、折叠、dismiss 或交接（[设计与裁定](working/proposals/one-purpose-stateless-core.md)）。

统一入口 `chat.run(items, goal, ...)` 接受日志引用或原始消息，要求非空目的并放在请求末尾，返回 `(ctx, text)`。构造、行动、沉淀、反思、月年汇总和分身共用模型构造、工具绑定、记录、计费、取消与超时；`functions` 可给出本次可见工具的白名单。超时立即返回，若 worker 尚未退出则 `finalization=pending`，待它退出后后台完成归档。迟到的准备材料不会追加到已经关闭的运行。

引用写法只有一套（`segment.resolve_refs`）：唤醒 `#n` 与步骤 `n.m`（只在有这些地址的视图里）、日志编号、运行或旧段的 ctx、笔记名（可加 `@L12-28` 选原文件行段）、`policy:名字`、`log:窗口[@时刻][*条数]`（那一时刻之前的若干条聊天，默认 30 条）。黑板、构造规格和 `agents__fork` 都用它；坏引用报出具体那一行。

1. **构造**：每次唤醒先跑一次 kind `construct` 的运行，提供信息、提醒和洞察，行动自行决定怎么回应。系统提示照旧（self、身份与 `memory_system` 的记忆说明）；材料按变化快慢排列：接口职责 `construct.CONTRACT` 和检查清单（`data/notes/policy/construct.md`，缺省用 `construct.DEFAULT_POLICY`）、笔记目录与首行（最近修改的 80 份，按名字显示，并标字符数和行数）、黑板 `recent` 全文与其 `@引用` 行的折叠描述、todo 全文、最近三小时行动时间线、最近几次运行的目的、其它窗口未读与 @、等着她的事和被叫醒窗口的新消息（`screen.prepare` 不确认阅读，前面带 20 条接话）及面包屑。可见工具只有 `construct__assemble`、`search`、`read_note`、`read_log`、`read_actions` 与 `agents__fork`。`assemble(focus, refs, note, windows, tools, history)` 提交关注情境、材料、观察、窗口、开场工具模块和接话条数；当场校验引用、工具和窗口，坏引用退回。引号只表示引用，不自动查笔记。模型、思考等级和超时在 storage `agent.construct`（旧 `agent.observer` 第一次读到时搬入），默认 `deepseek/deepseek-flash`、不思考、45 秒。
2. **规格与兜底**：每一步只交一份规格（2026-11-01 起，设计文档第 18 节）：`assemble` 要求规格至少负责一个被认领的窗口，记下后把运行状态的 `end` 置真，构造随这批工具调用结束，再交会被退回。没被选中的召唤不补规格，这一步结束时随 `wake["claims"]` 一起释放，读线程马上再认领、再起构造。没有记录号的召唤（闹钟、循环、命令）只在行动的 `wake["acting"]` 含它的窗口或 `me` 时才在 `_confirm` 里确认。构造关闭、出错、超时或没交规格时，用黑板（`recent` 加其可解析的 `@引用`）为所有被认领窗口兜底一份，并在注意里写明原因和读不出的引用。
3. **行动**：这份规格跑一次 kind `main` 的运行，材料依次是规格解析出的引用、关注情境和观察（system，供参考）、输入框里还没发的字；运行目的固定为结合材料自主判断，再由 `prepare` 在每次请求前追加它负责窗口的画面。画面里上次看过的位置之后标出新消息，前面带构造指定的接话条数（默认 10，可为 0）；从没看过的窗口按 `max_events`（默认 80）给首屏；普通新消息超过 60 条只给最新 60 条并说明翻查入口，follow 期间到的不截。全局 `agent.read` 是唯一已确认读状态；首次模型输出才 `commit_read`，只确认该请求实际展示的记录。每次请求前先把它负责的窗口里还在防抖的召唤直接并进来（`attention.flush`，2026-11-01 起），叫她的人也记进这次唤醒；行动期间别的窗口到的召唤记为 deferred，唤醒结束时释放，交给下一次唤醒的构造。攒批规则由窗口种类声明（`windows.kind(..., whole, debounce, follow, hide_control)`）：QQ 按 `#debounce` 的配置攒，同一人续期；B 站、CLI、`me` 不攒。
4. **临时提示**：其它窗口未读、当前时间、面包屑和后台命令按请求重算，只放本次末尾 hint。行动等运行的末尾只留 todo 的召回入口；行动里的 todo 全文或片段由构造选择，构造只在开场读取一次全文。`#` 控制/调试输出不作为画面正文；Bot 自己经命令工具获取的结果保留既有回流例外。原始聊天记录与撤回、回声规则见[运行文档](runtime.md)。

### 沉淀

`settle.touch(windows)` 在每次有输出的行动后记下窗口（storage `agent.unsettled`）。到期条件：最后一次行动后安静 `quiet` 秒（默认 1200），或沉淀位置之后积攒超过 `backlog` 条聊天记录（默认 200），配置在 storage `agent.settle`；夜间反思前全部到期。计时器在内存里，到点只启动读线程（`agent.kick`，不标记新工作）；读线程没有召唤时才沉淀一个到期窗口，`stop` 是“有新召唤”或被取消，所以在轮次边界让路，之后重跑；读线程空闲退出前重新排计时器。

一次沉淀是 kind `settle` 的运行：材料是相关笔记（recent、窗口的群或人笔记、这段里说过话的人的笔记最多 5 份、当天日记、todo）、那段聊天原文（沉淀位置之后最多 300 条，多的下次接着）和那段时间负责这个窗口的行动视图（只取行动自己写下的行：目的、注意、思考、工具调用与结果，每条工具结果只留前 600 字；构造借给行动的材料不重复给；超过六万字只留最近的并提示用 `read_context`），目的由 `roles.settle_prompt` 生成并附上 `policy/settle`。可调用全部 meta（交接与反思不封嘴的裁定沿用到沉淀），提示里要求不改 global。正常结束、最后回复不再调工具且没有取消，才把 storage `agent.settled` 的位置推进到这次看到的最后一条；出错或没完成隔 10 分钟再试，被让路不算失败。沉淀的视图进 `data/notes/years.md` 年鉴。

### 笔记、唤醒与夜间反思

笔记是 `data/notes/` 下的普通 Markdown，改写/删除前备份到 `.history/`，内容不变不重写。self 放系统提示最前，她只能在夜间反思用 `edit_policy` 改，维护者可直接编辑或 `#prompt set`；global、recent（黑板）、todo、awaker、person、group 和 diary 照常读写。旧 self 与 todo 的一次性导入保留。

awaker 文件提供 `every/daily/at/when/on/follow/mute`；默认 `daily 04:00 reflect`。每分钟检查长期规则，间隔第一次见到时起算，定点错过三小时不补；`on` 在消息到达时认领。wait 用 `wake_me` 或 send 设置到内存等待表，消息或秒级超时只触发一次；`#awaker` 同时列文件规则和内存等待。直接在笔记写 wait 不装回，返回可定位提示。

她用 `loop("在做什么")` 显式开启连续循环，外部唤醒不会自动开启。每次唤醒收尾排下一轮，普通间隔两秒；每轮照样先构造再行动；无动作时逐次翻倍，最长半小时。`wait(seconds, reason)` 在循环内推迟下一轮，循环外也可单独排一次唤醒，保留理由。`loop(stop=True)`、维护者取消或夜间反思停止循环；loop/wait 重启丢弃。`loop_note_history` 从 journal 查备注，已发生的循环唤醒仍记入 `me`。

夜间反思占住中心执行线：先沉淀所有待沉淀窗口，月末/年末汇总日记，再开一次反思运行加载 `memory_system` 与 `data/prompts/reflect.md`（缺省用仓库 `mods/chat/reflect.md`）。材料给出上次反思以来行动、构造、沉淀的次数和沉淀列表，她沿沉淀、构造、聊天/行动记录和笔记版本找真实问题，改 self/global 或策略并记录 changes。`#reflect keep` 跳过沉淀。反思视图也归档；期间来的普通召唤和后到的 reflect 控制工作留待后续分派。

`chat.current_wake()` 仅在主运行及其工具里返回普通 dict，结束写 `wake["end"]`。构造、fork、summary、沉淀与反思的运行状态独立，不能借继承的上下文改掉主唤醒。相关窗口的 `^C` 从主唤醒取关联窗口，取消模型并停止该执行线的子进程。私聊主唤醒每五秒续一次“正在输入”。


## 多媒体与提示词笔记（图片、语音与音乐）

中心主聊天的多媒体档位由 op 用 `#image <mode>` 与 `#audio <mode>` 全局设置。名称与数字别名分别为 `off/0`、`lazy/1`、`eager/2`。
- **图片档位（#image）**：`off` 只给图片地址和已有描述；`lazy` 在请求时处理这次运行的新图；`eager` 在图片消息到达时后台预取。文字描述另由 `#image discribe` 开关，见下文。
- **语音档位（#audio）**：`off` 时聊天画面呈现 `[语音: 5秒]`（无时长时为 `[语音]`），不触发下载；`lazy` 时调用 NapCat `get_record` 转码或直链下载到 `data/tmp_files/<sha256>.mp3`（复用 15 天闲置清理），画面呈现 `[语音: 5秒, file:///...]` 供柚子需要时调用 `audio__listen` 倾听；`eager` 时后台拉取并通过多模态模型自动转录，画面直接呈现 `[语音: 5秒: {转录与语气描述}]`。
- **音乐卡片（music CQ）**：聊天中的音乐分享（`[CQ:music,...]`）在进入画面时被自动解析转换为结构化的 `[音乐卡片: 《歌名》- 歌手 (平台)]`，便于模型准确理解歌曲信息。

### 提示词笔记与缓存自动失效

图片与音频的默认描述提示词分别存放在普通笔记 `data/notes/vision.md` 与 `data/notes/audio.md` 中（若文件不存在则平滑回退到模块内默认常量）。提示词采用**动态哈希版本**（`auto-v2:<hash>` 与 `audio-v1:<hash>`）：
- 只要维护者或柚子修改了 `vision.md` 或 `audio.md` 的内容，提示词 SHA256 哈希值改变，系统在读取描述缓存时判定版本不匹配，旧缓存自动被淘汰并重新请求多模态大模型，无需人工清理缓存。
- 图片描述缓存保存在 `llm_system/description_cache`，音频描述缓存保存在 `llm_system/audio_cache`，两套缓存物理隔离，避免相互误清。

中心主聊天的图片档位由 op 用 `#image <mode>` 全局设置。名称与数字别名分别为 `off/0`、`lazy/1`、`eager/2`；历史布尔值兼容为 `False → off`、`True → lazy`，未设置时默认 `off`。`off` 只让聊天图片以地址和已有描述出现，不自动下载或生成新描述；内联图片会保存为本地文件，以便之后调用 `attach_image`。`lazy` 在聊天请求时处理这次运行自己的新图；`eager` 在图片消息到达时后台下载。行动画面里的聊天图片以图片 part 进入请求，语音档位和 QQ 表情注释也在这里套用（2026-11-01 接回；cf8f0d4 起画面只放原文，图片一直只剩 `[CQ:image…]` 字样）。构造运行看到的仍是原文，图只是一段 CQ 码。

`#image discribe on/off` 独立控制 lazy/eager 是否自动调用视觉模型生成并缓存文字描述，兼容 `describe` 拼写，默认 `on`，查看不要求 op。开启时，即使主模型能直接看图也会生成描述；关闭后不生成新描述，但已有缓存仍可使用。eager 与聊天请求遇到同一内容时等待同一个进行中任务，避免重复识别。

**图片以运行为界变旧**：一次运行自己写下的行（行动的画面、`attach_image` 的结果）里的图，在它的工具续轮里一直按档位展开；从别的上下文借来的行——构造带进来的旧材料、fork 继承的引用——由 `run` 标成旧图，只发送 `[图片(URI)]` 或 `[图片(URI)识别结果：描述]`，不重新下载或付费描述。需要再看时调用 `attach_image(uri, note="", model="")`。主动附图在 `off` 下也放行，长截图照常切片；支持工具结果带图的模型在工具消息里看，不支持的视觉模型通过带来源标记的 user 消息看，这条消息只活下一次请求、不进经历日志，结果文字里留着地址；发给供应商前移除所有标记。指定 `model` 时由该视觉模型转述；主模型无视觉时自动调用默认视觉模型。

GIF 按实际文件内容识别。DeepSeek 自动看图保留原始 GIF；其他主模型默认只收到第一帧 PNG，并明确提示这是 GIF、可用 `attach_image` 查看完整过程。主动附图和自动描述都把 GIF 的**每一帧**依次排进网格，每张最多 25 帧，按帧的宽高比选择行列，超过就分多张。每格有全局帧号和累计时间戳，透明与增量帧先合成，不抽样，也不把网格当作长截图再次切割。[DeepSeek 支持 GIF 格式](https://api-docs.deepseek.com/guides/vision/)，这不表示服务端保证理解动画的每一帧。

Files API 只对 `deepseek` 供应商启用，默认开启，可在该模型配置中显式设 `files_api: false`；其它供应商即使登记 `files_api: true` 也不会上传。图片第一次发送时上传，之后用 `{"type": "file", "file_id": ...}` 引用。file_id 按内容摘要保存在 `llm_system/image_files`，有效期 7 天，仍在使用且离过期不到一天才重传，不用的到期后不续；上传失败后 5 分钟内改为内联。缓存同时保存切片/GIF 阅读说明，旧格式记录首次使用时重新上传。[Files API 的格式与限额](https://api-docs.deepseek.com/guides/files_api/)由对端规定。

一次 `chat.chat()` 持有期间，同一张图只解析一次，失败也不逐轮重试；本地文件消失、返回内容不是图片等不会自行恢复的失败跨对话记一小时，网络失败不记。成功图静默复用，只有下载、上传和失败才记录提示。中心 agent 可按需加载 `image` 工具模块：`recognize_image` 不依赖窗口，生图返回临时文件 URI，由模型再通过 `type_word`／`send` 发到显式目标。`vision__storyboard(video, count=9, cols=3)` 用本机 ffmpeg 从视频文件或流地址均匀抽帧，拼成带时间戳的分镜图，返回它的 `file://` 地址。

自动描述使用固定 prompt，要求直接概括可见内容、转录重要文字、标明不确定项，并禁止“如果你愿意我还可以……”一类元话术。自动聊天图片处理、eager 预取、`recognize_image` 和参考图生图共用图片解析入口：`http://`、`https://` 和本机绝对 `file://` URI 都解析为 SHA-256 内容身份；网络图片按摘要缓存在 `data/tmp_files`，本地文件只计算摘要而不复制。所有入口都执行图片格式和 20 MiB 上限校验。需要把图片交给视觉模型时，再统一由 `image_uri_to_data_uri` 编码。`recognize_image` 支持自定义识别 prompt，不设置输出 token 上限，也不读写自动描述缓存，避免长文本识别被截断以及不同识别任务互相串用答案。

视觉模型实际收到图片前会检查已经编码的图片尺寸。若图片高度严格大于宽度的 4 倍，原图会被仅在内存中切成从上到下排列的多个 PNG 片段：每片从 3 倍图宽的整数倍高度开始，先覆盖 3 倍图宽；若后续仍超过 1 倍图宽则向后延伸四分之一图宽作为重叠，否则直接延伸到末尾，因此片段最高为 4 倍图宽。这些片段替代原图，并附带 `【自动图片切割】` 提示，让模型按一张连续图片理解并避免重复转录。原生视觉聊天和生成图片描述共用这项处理；长图分片只在请求中使用，自动描述仍按原始图片的内容摘要缓存最终结果；主动打开 GIF 的网格图会保存为临时文件引用。生图参考图不经过这项识别预处理。

新生成的描述缓存以图片 SHA-256 为键，值包含 `description`、`cached_at` 和自动描述 prompt 版本。迁移脚本也能保留 QQ URL 自带内容摘要的历史描述：`multimedia` 使用 `sha1:<摘要>`，`gchat` 使用 `md5:<摘要>`；这些迁移键只用于命中描述，不伪装成可解析原图的 alias。只有视觉调用成功才写入新描述；lazy 和 eager 对字节相同的图片直接复用文字，即使来源 URL 不同。进行中识别以“内容摘要、prompt 版本”合并，等待方将同一结果写入各自的描述缓存。

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
| `exec_code` | 在 `.py` 的共享 `loc` 中先 `exec(code)`、再 `eval(expr)`；`timeout` 默认 15 秒（`0` 表示不限，但只在后台模式允许），代码跑在一个可终止的子线程里，到点终止它启动的子进程并中断该线程；`back="wake"`/`"quiet"` 立刻回执，跑完把结果推进 `me` 窗口，后台跑在独立线程、不占 `_tasks` 的单队列（`timeout=0` 可能无限等）。`print` 的捕获用 ContextVar 跟着这次执行走，用户代码自己起的线程也会带上同一捕获流；不改写共享 `loc`。调用时仍检查 op，拥有与 `.py` 接近的进程和宿主机能力。 |
| `list_tools` | 列出 last-good 模块、当前会话激活状态、空闲回收规则、最近加载失败及磁盘差异。 |
| `reload_tools` | 按模块名从磁盘显式应用、更新或删除模块；逐项返回成功和带完整 traceback 的失败结果，失败保留旧 last-good。 |
| `load_tools` | 将 last-good 模块激活到当前 Chat；中心名单存全局 `agent`，不读取磁盘。 |

`meta` 还提供明确目标的 `type_word/send`、规则 `wake_me`、召回 `search/read_log/read_actions/read_note`、笔记 `write_note`、连续活动 `loop/wait/loop_note_history`、`set_name` 与 `attach_image`（2026-11-01 删除了 `tidy/dismiss/compress/decompress`）。工具模块 `construct` 只有 `assemble`，只在构造运行里可见、可用。发送先打字、下个模型输出再发送；输入框存在 storage，跨唤醒和重启保留。`send(wait=0)` 继续工作，`wait=-1` 收工被动等，正时长则挂回复或超时的一次等待，可用 `who` 限定作者；群里不写 `who` 时，这次唤醒若是某人叫醒的就默认只等这个人，写 `who="any"` 等全群（2026-10-31）。时长后面可以直接跟理由，如 `wait="5m 等他接话"`。CLI 窗口（[CLI agent 窗口](cli-agents.md)）把整段输入作为一轮任务，不拆气泡。

仓库当前提供以下按需模块；函数只在对应模块激活后出现：

| 模块 | 导出能力 |
|---|---|
| `common` | `common__get_time`、`common__poke` |
| `image` | `image__recognize_image`、`image__create_image`、`image__create_image_from_references` |
| `vision` | `vision__ask`、`vision__models`、`vision__stitch`（多图拼成网格或把图交给外部视觉模型认字比对） |
| `audio` | `audio__listen`（听取语音条或音频文件，转录内容并分析说话者口吻、语气情绪与背景杂音） |
| `later` | `later__later_add`、`later__later_del` |
| `user_data` | `user_data__get_user_data`、`user_data__set_user_data` |
| `agents` | `agents__fork` |
| `amap` | `amap__login`、`amap__search`、`amap__around` 等地点与路线查询 |
| `websearch` | `websearch__search`（调用 DeepSeek 服务端 `web_search` 检索互联网网页） |
| `bili` | B 站视频推荐/热门/搜索/详情/字幕原文、评论区与楼中楼、评论赞踩与删除、视频点赞投币与收藏夹管理、个人主页与资料修改、动态与关注列表、消息中心 `inbox`、私信 `dm` 与 `use_cookie` |
| `browser` | 自管常驻 Chromium（按窗口独立标签页）：`open_page`、`read_page`、`page_links`、`run_js`、`look`、`screenshot`、`frames`、CDP 真实输入（`click`、`type_text`、`drag`、`press_key`、`scroll`）与 Cookie 读写 |
| `phone` | 安卓备用机控制（scrcpy 视频流与控制通道 + adb）：`look`、`frames`、`tap`、`swipe`、`hold`、`release`、`mark`、`aim`、`key`、`type_text`、`keyboard`、`shell` |
| `mailbox` | IMAP 邮箱账号保存、文件夹与邮件列表查看、邮件正文读取 |
| `totp` | TOTP 两步验证码密钥管理与实时动态口令生成 |
| `host` | `host__read_file`、`host__write_file`、`host__run_command`、`host__stop_command` |
| `cmds` | `cmds__list_commands`、`cmds__run_command`（查询或代行普通点命令） |
| `op` | `op__send_command`（`BOT_OP_ONLY`，以 Bot 自身身份向主事件队列投递命令，含 `.reboot`） |
| `forward` | `forward__read_forward`、`forward__send_forward`（读取或构造合并转发聊天记录） |
| `sendfile` | `sendfile__send_file`、`sendfile__send_text`（在聊天窗口直接发送本地文件或长文本文件） |
| `avatar` | `avatar__avatar_url`、`avatar__group_avatar_url`（取 QQ 用户或群头像地址） |
| `minecraft` | `minecraft__search_mc_mod`、`minecraft__check_mod` |
| `meme` | 表情柜子：`meme__collect` 把聊天里或网上的图下载进 `data/memes/<sha256>.<扩展名>`（同一张只存一份），连同她起的心情和一句话记进 storage `meme/index`；`meme__pick` 按心情或关键词随机挑一张（优先最久没发的），返回带 `sub_type=1` 的 `[CQ:image,file=file://…]`，她再经 `type_word`／`send` 发；`meme__browse` 看总览、`meme__edit` 改或扔 |
| `tts` | `tts__speak`：用 edge-tts（微软 Edge 在线朗读，非官方、免账号）把文字念成 mp3，默认音色晓伊 `zh-CN-XiaoyiNeural`，存进 `data/tmp_files` 返回 `file://` 路径；她再用 `[CQ:record,file=…]` 经 `type_word`／`send` 发语音 |
| `weather` | `weather__search_city`、`weather__get_realtime_weather`、`weather__get_daily_forecast`、`weather__get_hourly_forecast` |
| `memory_system` | `read_context`、`note_history`、`read_policy`、`edit_policy`、`assess_change`（夜间反思时加载，回看归档上下文、笔记历史版本与策略账本） |
| `term` | `term__look`（命令行窗口渲染后的屏幕或最近若干行历史）、`term__keys`（按 tmux 键名，如 `C-c`）、`term__close`、`term__terminals`；`BOT_OP_ONLY`。往 `term:名字` 打字发送走 `type_word/send` |
| `yuzu_hello` | `yuzu_hello__hello`（最小示例工具模块） |

`host` 是 `exec_code` 之外的常用出口：文件读写和 shell 都不必再绕一次任意代码执行。读取按本次实际截取的窗口计字节，超过 `max_bytes` 时只回大小和后续建议（缩小区间、先 `grep -n` 定位、或用 `fork` 开子会话通读后带回摘要），不做分页会话。改写靠 `read_file` 首行 header 里的 `line`/`size`/`v` 定位与对账：位置与新内容的写法无关，`v` 只在该区间自读取后被改动过时失配，失配的返回自带现状和新 header。渲染格式与 `.edit` 同源，所以手动编辑和模型改写看到的是同一种东西；它没有目录白名单，`data/`、`config.json`、`.env` 和聊天记录都在可及范围内。

`run_command` 默认等 15 秒：跑完直接返回退出码和输出；没跑完不杀，转后台并回任务号、`data/commands/<任务号>.log` 的绝对路径和已有的输出。后台进程的 stdin 是 `/dev/null`，stdout 和 stderr 合流进同一个文件；默认 wait 等到 15 秒转后台时会叫醒她（等同 wake），`timeout=0` 表示立刻转后台，不是无限等。`back="wake"`/`"quiet"` 可以一开始就转后台，跑完把退出码和最后几十行推进 `me` 窗口（quiet 不叫醒）；`stop_command` 按任务号停掉整个进程组，连同子进程。后台命令各自一个进程，不排 `tools._tasks` 的队列；还在跑的命令会出现在每轮请求末尾的提示里，每次重新算、不存。`data/commands/` 下每个任务还有一份 `<任务号>.json` 记录进程组，正常退出时收掉，崩溃重启后按它接管还活着的组。

除 `weather` 继续投影可用的 `mods.weather` 函数（其 schema 描述来自 `mods.weather` 函数自身的 docstring，与命令的 `-h` 帮助同源）外，这些文件持有各自工具的真实实现，不再从 `mods.chat` re-export。图片和分身通过统一 `chat.run` 计费，状态仍只有一份。

历史源码中有实现、但 `add_tool` 注册被明确注释的 `read_data`、群成员、农历/小六壬、跨窗口发送、`later_list/later_set`、URL 转 CQ 和百科工具，只在 `mods/tools/disable/README.md` 留有决策记录：说明它们当时是什么、现在等价能力在哪里、以及 RAG 等只剩草稿或死名字的项为什么不复活。仓库不保存永不运行的实现；要启用就按现有模块格式在 `mods/tools/` 顶层重写。

`create_image` 使用 OpenAI 原生 `gpt-image-2` 的参数与返回形状，不传 DALL·E 的 `style`、`standard` 或 `response_format=url`。图片以 `b64_json` 返回，解码后存进 `data/tmp_files` 临时图片缓存，工具只返回 `file://` 路径，不自动发送；Base64 本身不会进入 QQ 消息或 chatlog。缓存文件闲置超过 15 天可能清理。

`create_image_from_references` 的 `image_uris` 参数每行接收一个 URI，并在输入边界按完整 URI 去重。解析后复用统一的内容寻址文件缓存；`file://` 必须是本机绝对路径。所有参考图都需通过图片格式和 20 MiB 上限校验。`gpt-image-2` 会自动高保真处理参考图，请求不传 `input_fidelity`。供应商是否对参考图输入另行计费尚未实测；当前 usage 仍只按实际返回图片数以每张 0.13 元记录。

退役窗口会话后，四个原本依赖隐式来源的模块改用中心可调用的边界：`image` 生图只回 `data/tmp_files` 内容缓存的 `file://` 路径，模型再把 `[CQ:image,file=...]` 用 `type_word(target="g…"/"u…")` 打进输入框、`send` 发送，CQ 正文不转义；`later` 增删任务必须给明确目标窗口；`amap` 的 Key 存在 Bot 自己的用户 storage，不再按唤醒作者查；`agents__fork` 的子会话清除当前事件、不继承隐式窗口，结果只作为父工具返回。`common__poke` 也要求中心会话明确目标。Bot 非 op 时仍可经 `send` 发送已生成的本地图片，但读取任意本地 `file://` 参考图或识别图仍需 op。

## 统一模块格式与两种加载

`mods/tools/` 只扫描顶层、不以下划线开头的 `*.py` 和 `*.md`。同 stem 的 Python 与 Markdown 文件冲突；子目录不递归扫描，可以由顶层内容引用或由 Python 正常 import——这是有意的分层而非遗漏：模块目录是常驻上下文，递归会让子文件夹的内容一开局就全部占位，只列顶层则把它们降为"展开之后按需索引"的一级，与首行/全文的分层同理。两种文件共享以下最小格式：第一行是总会出现在模块目录中的描述，后续全部是激活后内容，不设 front matter、summary 或另一套 skill 协议。Markdown 到此结束；Python 还必须显式声明 `__all__`，其中可列零个或多个同步函数。

`mods/tools/__init__.py` 用模块级函数和 last-good 字典管理目录；`bind_session(...)` 返回每次运行自己的普通 dict，`current_binding()` 在工具调用范围用 ContextVar 取得它，后台线程传播同一归属。`meta.py` 才是已经激活的基础模块：它和其它 Python 模块一样使用第一行 summary、后续说明、普通函数与 `__all__`，但作为必需恢复入口从开局就激活全文，四个函数名不加 `meta__` 前缀。其它顶层模块仍只默认显示第一行，显式激活后才加入余下内容。`meta` 必须**至少**导出那四个恢复函数（`exec_code`/`list_tools`/`reload_tools`/`load_tools`），并同时承载始终可用的聊天、信源和记忆工具；校验只读「少了哪个」（`_BASE_TOOL_NAMES` 与 `absent` 判定），不冻结额外导出数量。原先要求 `__all__` 与那四个完全相等是迁移带来的附带收紧；基础模块还要增加其它始终可用能力，精确相等会让整个恢复入口加载失败。删除其磁盘文件后 reload 会失败并继续保留旧 last-good，而不是卸掉恢复入口。

模块可以在顶层写 `BOT_OP_ONLY = True`，声明"只有 Bot 自身拥有 op 权限时可见、可加载"（目前只有 `op.py` 这么做）。Bot 的权限来自 `config.bot_permissions.op`，在一次进程生命周期内固定，不读取 `context.current()`，因此不会因这一轮读到谁的消息而出现或消失。三层都拦：渲染目录时过滤（`tools.bot_op_tool_visible`）、`tools.load(binding, ...)` 拒绝（模型记得名字直接 load 也没用）、工具执行时再查一次 `op.bot_is_op()`。

中心 reader 的模型与辅助调用记入 Bot 的全局月账，不随唤醒作者或 `context.current()` 改变；旧月份不迁移。`cmds__run_command` 留空 `sender` 时以 Bot 自身执行，显式代行人类 op 时 Bot 自身也必须有 op 权限；下游仍按被代行者判权。`message.recvmsg(sender_id=X)` 只在可编程环境中使用，不直接暴露为模型工具。

Python 候选作为正常模块执行，可以 import 第三方依赖、其它 `mods` 和同目录下划线 helper。每个导出函数都必须有可用的签名、参数类型标注和 docstring，并通过现有 `Tool` schema 校验；模块内任一导出失败，整个模块都不替换。加载候选不会调用导出函数，但会执行顶层 import 和其它顶层语句，所以这里与 `.py`、命令、link 和宿主机操作属于同一信任域，不是沙箱；顶层应只放 import、常量和定义。

首次使用模块目录时，模块级 registry 会独立尝试每个文件，成功者成为进程级 last-good，失败者记录 traceback 而不阻断其它模块或 Bot。运行中有两种不同操作：

- `reload_tools(names)` 读取磁盘并逐模块应用变化。成功才原子替换 last-good；失败保留旧版。源文件已删除时，显式 reload 同名模块才删除 last-good。
- `load_tools(names)` 完全不读磁盘，只把 last-good 模块激活到当前 Chat；行动的加载只在本次运行内有效；反思和沉淀的加载名单写全局 `agent.active_tools`。曾发送过的函数定义即使卸载仍保留，但调用只返回重装提示。

因此改文件本身不生效；末尾 drift hint 只报告磁盘差异，不自动加载。`list_tools` 在调用时报告 last-good、当前激活状态和磁盘差异；没有 watcher。Markdown 与 Python 服从同一手动应用生命周期。成功 reload 替换进程级 last-good；中心会话在下一请求边界同步共享 registry，再登记变化。

### 激活属于会话主体

行动以构造指定的工具集合开场，不继承也不修改持久激活集合；本次仍可自行 load_tools。反思和沉淀的实际加载集合保存在全局 `agent.active_tools`，每个模块保存最后装入或调用的时刻；`tools.restore(binding, ...)` 在新一轮开局按它恢复，闲置超过 1 小时的模块不再激活。`touch` 在工具结果后立即写回使用时刻。权限暂时不足的模块保留原时刻在激活名单里，恢复权限后仍可装回；源码已删除的模块才从名单删除。子代理不继承中心激活名单。

函数定义的生命周期与激活分开：视图的工具状态条目保存模块首次加载的 schema 顺序；卸载后定义仍在请求中，但调用只返回先 `load_tools` 的提示。重启从当前 last-good 代码恢复定义，不保存旧 schema。调用同一请求里的函数始终使用请求开始时冻结的 callable；共享 registry 经其他会话 reload 后，中心在下一请求边界同步本会话函数再告知变化。`list_tools` 返回完整当前状态和磁盘差异。

### 追加式上下文

`Chat.add_context_provider` 的产物在每次模型子请求前追加进 `Chat.messages`，前面的消息不改。中心会话用它追加一次唤醒内的工具状态变化：每次子请求先 `reconcile(binding)`，从视图推导上次快照，追加完整或差异状态；已发出的 schema/callable 快照保持不变。所有运行通过 `chat.run` 在请求边界同步 binding，再追加工具状态条目。

磁盘源码差异是末尾 hint，只报告、不加载。模块正文内的 `</system-reminder>` 会转义，避免逃出系统框架。

### 末尾 hint

hint 与追加式上下文是两层，判据是一句话：**频繁变化、且随时可以重算的状态放 hint；"发生过一次"的事实放 provider。**

- provider 追加进 `Chat.messages`，进历史、可回放、会一直留着。
- hint 每次模型子请求重新渲染一次，只挂在**发出去的那一份**末尾，从不写回 `messages`。旧的自然消失，上下文里不会堆出几代互相矛盾的副本。
- 与 system 提示词一样支持用函数生成（`Chat.add_hint` 接受字符串或可调用对象），只是重置时机不同：system 在建会话时定一次，hint 每个子请求重来一次。
- 因此 hint 里只放随时可重算的东西，不放"只此一次、错过就没有"的信息——那种必须走 provider。

中心待办是笔记 todo：构造输入读取一次全文，行动由 refs 选择全文或 `todo@L起行-末行`；末尾只留召回入口。沉淀显式载入 todo，反思与分身可主动读取；不再每轮末尾重复摊开全文。长期可复用经验可写成 `mods/tools/*.md` Skill，经 `reload_tools` 应用、`load_tools` 按需插入。关于人、群和跨场合的事，她当场或在沉淀时写进笔记；原话不复制，需要时按窗口和时间用 `read_log` 读回。

`tools.drift_hint(binding)` 报告 `mods/tools` 磁盘源与 last-good 不一致的模块名；只报告不加载，也不重复工具正文。它每次请求重读磁盘，文件改回去，提醒就自行消失。


### 中心工具状态

每次运行首次组装时给出完整工具目录与已加载模块说明书（含 `meta`），紧跟在系统提示之后、本次材料与目的之前（2026-11-01 起；以前排在材料之后，每次运行都要重新计费）；这次运行里不再重发。一次唤醒内的变化由 provider 追加（见上）。工具状态是 journal 条目，后续快照从当前视图推导。

`active_tools` 保存反思和沉淀的持久加载模块及最后使用时刻；这些运行的工具调用会刷新并写盘，行动的临时选择不写回。视图的工具状态条目记录模型曾获函数定义的模块顺序：`meta` 固定最先，其它模块首次加载后追加；空闲卸载不删除定义，但下一请求中的对应函数会只返回"先 `load_tools`"，不执行已卸载代码。同批工具调用仍使用发请求时冻结的 callable。重启后按保存的模块顺序和**当前代码**重建定义，不保存旧 schema 快照。`list_tools` 可随时取完整最新状态。

2026-11-01 切到无状态后实测缓存命中从 96% 降到 80%。随后调整前缀：工具宣告放到系统提示之后；构造材料按变化快慢排列；行动材料（`assemble` 的 refs）按 global、其它笔记与策略、人物与群、recent/todo/日记、日志与运行记录排序，同一档保持原顺序，最后一档不打乱。在合成的三次唤醒中，相邻两次首轮请求的共同前缀，构造运行从 29% 升到 95%，行动运行从 44% 升到 98%。生产上的命中率仍以部署后 usage 的缓存命中数为准。

### 中心设置与旧窗口配置

`image`（图片档位）、`image_describe`（图片文字描述）、`reasoning`、`reasoning_effort`（思考等级）、`temperature`（采样温度）、`brake`（节奏刹车）、`max_events`、`tidy_token` 共用 `mods.chat.AGENT_SETTINGS` 的全局归一化规则。中心主会话只读全局 `agent` storage；op 用 `#use_model`、`#image`、`#reasoning`、`#effort`、`#temperature`、`#brake`、`#limit`、`#construct` 等命令修改它。`#temperature` 是 `default` 或 0.0–2.0；`default` 时 effort 为 `none` 的运行用 1.3（DeepSeek 对非思考与创意写作的推荐值），其它交给模型默认；构造运行另有自己的 effort，不跟着升温。`#brake on`（默认）在行动运行的提示末尾加一段英文的节奏与视角约束（只推进一个反应、不替对方说话、不全知、不重复细节、不煽情），不进经历日志；构造、沉淀和反思不加。旧窗口聊天配置不自动迁移，也不再读取；`#hint` 的窗口配置是独立例外。

- **缺省值写死在代码里**（`DEFAULT_MAX_EVENTS` / `DEFAULT_TIDY_TOKEN` 与各归一化函数的兜底分支），不再读 `llm_system/config.json`。中心运行期覆盖只有全局 `agent` storage 一份；`#limit` 是唯一预算命令入口。
- **两个预算**：`max_events`（默认 80，存储键沿用旧名）是从没看过的窗口的首屏条数；`tidy_token`（默认 500000，存储键沿用旧名）在 2026-11-01 以前是交接阈值，现在只限制一次反思或沉淀里材料之后新长出来的部分。2026-10-22 以前是 `max_token × pressure_percent` 两个数，那是按每次重组上下文定的预算，第一次读到时删掉旧键、用默认。旧窗口覆盖不自动迁入全局主体。每晚反思的时刻在笔记 awaker 里，`agent.reflected_at` 记上次反思的时间。旧的 `agent.librarian_model`、`agent.tidied_at` 不再读取。
- **`hint` 与 `prompt` 不在表里**：窗口结束提示的 `#hint` 是 `data/hint/` 下的文件，独立合并全局默认与窗口覆盖；她对自己的认识是笔记 self（`data/notes/self.md`）。它们不是单值档位，不使用 `AGENT_SETTINGS` 的归一化。
- 已退役的窗口聊天设置仍可能留在生产 storage，但运行代码不再读取；其清理需先获维护者授权，见[第二轮调查](working/round2-legacy-and-backfill-audit.md)。

**召回画面以外的内容。** 中心 agent 用 `read_log(window, count, before)` 读任意窗口的 chatlog 原文（格式与画面相同，`before` 写 `YYYY-MM-DD HH:MM:SS` 或 Unix 秒，用来往前翻），`search(keyword, window, days)` 按子串一次找遍四处，按出处分组：一个或所有窗口最近若干天（默认 30，最多 365）的聊天记录，最新的 30 条在前；所有笔记和日记（不限时间，每份列前 3 行命中，最多 20 份）；同一时间范围内归档的段（只找 assistant 的思考、正文、工具调用和工具结果，不找画面、笔记、系统消息这些别处有原文的 user 与 system 消息，每段列命中数和前 2 处，最多 10 段，最新在前）。`read_actions(ids, count, keyword, start)` 读自己的行动记录，`read_note(scope)` 读笔记。旧的 `take`、`status`、`mentions`、`fetch`、`read_messages`、`mark_read`、`pull` 已移除。`#hint` 不触发阅读。

### 行动记录

每个完整 assistant 输出先取得稳定 `YYYYMMDD-N` 号并落 journal，再执行工具。派发前记录 `tool_started`，每个工具完成后立即记录它的原生 tool 结果，最后关闭逻辑 act。取消后已完成结果仍在；未执行、结果未知与中断由读取投影说明，恢复不自动重跑外部动作。当前运行的写入失败停止后续派发并报告；下次独立运行可重试，没有 oplog 的进程级熔断。

`read_actions` 按正式号、最近条数或关键词分页读可读行动；`read_context` 渲染冻结视图。chatlog 继续负责窗口原文。思考和工具原参数都保存在 journal，`#reasoning drop`（以及旧视图里 compress 参数的缩短）只影响投影，不删原文。


### 思考内容开关与思考等级（Reasoning Effort）

原生 assistant 消息与 `reasoning_content` 保存在 journal。`#reasoning keep`（默认）带回历史思考；`drop` 渲染时去掉旧唤醒和借入材料的思考，本次运行仍在进行的工具链保留。切换回 keep 或读取原始条目仍能取得完整内容。

**思考等级（Reasoning Effort）。** 除了历史思考是否带回上下文，底层 `mods/llm/` 级别原生支持控制模型生成思考的深度与开关（`reasoning_effort` 参数）：
- **可选档位**：`default`、`none`（别名 `off`/`0`）、`low`、`medium`、`high`、`max`。
- **API 映射逻辑**：`generate_response` 与 `Chat` 在组装向供应商 API 发送的请求参数时，若 `reasoning_effort` 为 `"off"` 则自动归一化为 `"none"`；若属于 `"none"`、`"low"`、`"medium"`、`"high"`、`"max"`，写入请求参数 `params["reasoning_effort"] = effort`；若为 `default` 或 `None`，不向 API 注入该字段，完全保持模型自身的默认行为。
- **原生关闭深度思考**：针对 DeepSeek 系列模型（如 DeepSeek-R1、DeepSeek-Flash 等），下发 `reasoning_effort="none"` 可原生彻底关闭深度思考输出，实现秒回；在轻量旁观任务中尤为有效。
- **`#effort` 管理子命令**：中心主模型的思考等级保存在全局 storage `agent.json` 的 `reasoning_effort` 键中，默认 `"default"`。管理员可通过 `#effort [default|off|none|low|medium|high|max]` 命令查看或设置；`chat.run` 初始化模型时赋给 `session.reasoning_effort`。


### 窗口的结束提示（`#hint`）

这是与上面[末尾 hint](#末尾-hint)一节不同的另一个「hint」，两者只是碰巧同名：那一节说的是**发给模型的那一份请求末尾**挂什么，这里说的是**用户存给某个聊天窗口的一段代码**，它会在这个窗口的聊天循环停下时被求值并发送。

命令面只做文本管理——写、看、开关，和 `#model`/`#prompt` 一系；「它在聊天结束时自动跑」是另一回事，两半分开看。

- **谁跑、什么时候跑。** 唯一中心 reader 在 `_read_work` 收尾调用 `_run_hint`，按本次关联 QQ 窗口选择窗口 `#hint` 配置；这是向 QQ 发状态的旧机制，不是全局模型待办 hint。无 reader 所有权的早退不触发。
- **配置模型。** 两份普通文件（2026-10-22 起）——全局默认 `data/hint/default.py` 与窗口 `data/hint/g<群号>.py`／`u<QQ号>.py`，首行 `# on` 或 `# off` 是开关，其余是代码；以前存在 storage 的配置在第一次读到时搬进文件并删掉旧键——读成字典后按显式顺序合并成 `{**default, **window}`，两侧都只认同名的 `code`/`on` 两个键，没有别的间接层。发不发 = 合并结果里 `code` 非空**且** `on` 为 `True`；`on` 缺失按 `False`。窗口可以只写 `on`（单独关掉默认提示，`code` 仍继承）。`#hint set <源码>` 写入 `{"code": …, "on": True}`，`#hint set` 无参清掉窗口配置、回落默认；`#hint` 与 `#hint default` 只翻开关，保源码。没有 `del`——源码是劳动成果，toggle 不该顺手删掉它。
- **求值。** 走 `py.eval_last(source, environment)`，与 link 共用同一份实现（原先 link 里那份私有副本已消掉）：前面各行 `exec`，末行 `eval`；末行为空、以 `#` 开头、或结果为 `None` 就不发。环境是共享动态环境 `py.loc` 的**一份副本**，另注入 `window`（本窗口的 `history.window` 键）、`usage`（这次唤醒最后一次子请求的上下文 token 数）、`peak`（这次唤醒里最大的一次）与 `limit`（`#limit` 的整理阈值）。后三项在中心 agent 仍活跃时冻结，避免结束后误读不相关的窗口状态。
- **输出带 `#` 前缀**（`"#" + cq.escape(str(result))`），因此不回流进模型上下文，也不自指。报错同样以 `#` 开头、附 traceback。
- **异常安全是硬要求。** 求值与发送的任何异常都在这一层吞掉、只写 `hint` 日志流，**绝不抛回 `finally`**：这段代码是用户自己写的、每次聊天都自动跑，让它抛出去就等于一段烂代码能污染聊天主流程的返回路径。（只吞 `Exception`——`SystemExit`/`KeyboardInterrupt` 是进程控制，不该被用户代码吃掉。）
- **「已用上下文」的出口是 `chat.context_usage()`**。中心 agent 在每个工具批次结束时记录这一段的 token 数：供应商上次返回的 prompt+completion（含系统提示、工具 schema 与图片），加上那之后追加的工具结果与画面的本地文本估算；还没有返回过时（新的一段）全用本地估算，图片按占位计。`#hint` 的代码里可用 `usage`（这个估算的最后一次值）、`peak`（这次唤醒里最大的一次）与 `limit`（整理阈值）；旧的 `event_count`、`context_limit` 已删除。
- **op 专属。** hint 是用户可写、跑在特权环境、还每次聊天自动执行的代码，权限与 `.py`/`.link` 同级，比只改提示文本的 `#prompt` 高一档。非 op 的 `#hint` **不接管**（`cond()` 里先判权，返回 `False`），只给一节流提醒——复用 `op.require_op` 的约定，提醒的节流按「同一个人的同类重试」判（`pattern=r"^#\s*hint"`），所以提醒不会把别人正常的 `#hint` 也算进去。
- **`#help [名称]`** 照 `.help` 的两级形态：无参列所有 `#` 子命令的首行摘要（`#{pattern} — {首行}`），带名称显示那一条的完整多行说明，查不到就是 `该命令不存在！`。**不做 op 过滤**——单 `#help` 列出全部（含 op 专属的 `hint`），非 op 真去执行 `#hint` 时才被拒：`#help` 是所有人的发现入口，为它加特判不值。

命令目录见 [commands.md](commands.md)；取舍、被排除的做法与推翻条件见[窗口级 `#hint`](working/proposals/chat-hint.md)。

## 一轮工具调用怎样继续

`chat.run → Chat.chat → LLMClient.chat` 同步请求模型；每个子请求冻结 schema/callable，完整输出落 journal 后按顺序执行该输出的工具，每项结果立即记录，再准备下一请求。只有模型配置声明 `function_calling=true` 才发送 schema。load/reload 从下一子请求生效，同一响应其它调用仍用旧快照。

普通工具返回转成字符串，异常回传类型与错误文本、完整 traceback 留日志；reload 失败仍回完整 traceback 便于修工具。未知工具名和非法 JSON 参数都返回说明性结果。模型正文是自语，不直接发往 QQ。

入站在窗口锁内先写 chatlog/history，再经 attention 登记读状态与唤醒。防抖在 attention 内，对 QQ 默认等三秒，同窗口同作者续话才重置，最多十二秒；戳一戳与定时唤醒立即处理。相关窗口新到的材料在下次模型子请求前加入视图，其它未读每次提示。

`^C` 先取消该用户线的 continuation，再取消相关中心唤醒并 `watchdog.stop(AGENT_WINDOW)`。LLM 在轮间和流式 chunk 检查停止，每项工具派发前也检查；已发生外部动作不撤销。watchdog 只登记工具调用期间 spawn 的子进程，结束后退出登记，避免后来误杀常驻服务；调用工具的线程本身有意不登记，避免文件写一半被掀翻。卡在首个 chunk 或不可打断系统调用时仍有检查点限制。`exec_code` 的调用方用子线程超时拿回控制权，线程可能尚未退出。

主工具循环没有全局最大轮数、总预算或副作用回滚。单次模型 HTTP 请求有 `request_timeout`（默认 120 秒，每个流式 chunk 续计），独立 `run` 也可指定 timeout；这不承诺外部服务恰好执行一次。

## `fork` 的分身分派

`agents__fork(goal, refs="", tasks="", tools="", model="")` 按引用组装独立运行。refs 用上文那一套引用写法（笔记名、日志编号、ctx、`policy:`、`log:` 等），复用原条目，坏引用指出具体引用。tasks 每行一项，最多五项同时运行，结果按原顺序返回 `(任务, ctx, 结果)`；失败也给独立 ctx，能用 `read_context` 查已完成部分。

分身默认有 meta，与主运行分别绑定 active/schema 和唤醒状态。默认不暴露 `send/type_word`，tools 中逐行显式写函数名才授权，`me` 同时授权两者；重新 `load_tools` 不能绕过默认过滤。该授权只属于本次运行，仍使用 Bot 固定宿主权限。反思和沉淀的全局工具激活跨重启保留，行动由构造按次选择；schema 顺序来自各自视图。

所有模型调用经 run 把供应商返回的 usage 记入 Bot 账；费用按请求时刻的未命中、缓存命中和输出单价计算。缺 usage 暂无 token 估算计费兜底。`.chattop` 按自然月显示，调用次数与 HTTP 子请求数不同。


## 构造运行（原旁观助手）

2026-10-27 起的旁观助手在每次唤醒、主模型推理之前跑一次，点出陌生说法、矛盾、自我限制、情绪和行动里的意外。2026-11-01 它并入构造运行（`mods/chat/construct.py`）：同一个位置、同一套模型配置，产出从一段提醒变成行动的规格，提醒放进规格的注意。10-29 拿掉它的 `load_notes`，理由是挂上的整篇笔记会一直占着那一段；每次行动都是新上下文以后，构造可以按需在规格里带笔记。

- **职责与接口**：构造提供信息、提醒与洞察，行动自主决定怎样回应。`construct__assemble(focus, refs, note, windows, tools, history)` 中 focus 描述情境或问题，与 note 一起作为参考材料；不再直接进入行动的末尾目的。每次只交一份。
- **清单**：`CONTRACT` 说明职责和材料接口，随后加载 `data/notes/policy/construct.md` 的补充 checklist；不存在时用默认清单。保留陌生指代、矛盾、情绪、意外，要求依据与不确定性，不逐项交作业。引号自动查词已删除，构造显式调用 search/read_note 核对。
- **材料选择**：目录显示首行、字符数与总行数。refs 和 read_note 同时支持 `笔记名@L起行-末行`，按源文件从 1 起计行，越界拒绝；局部引用保留独立 scope、出处与行段，不冒充全文。tools 选开场的工具/Skill 文档及函数；meta 基础能力保留，开发说明移到 `tool_development.md` 按需加载。history 选新消息前的接话条数，默认 10，可为 0；未读选择与确认不受它替代。
- **聊天去重**：新 log 引用保存逐条版本身份；渲染时省略与本次 screen 或前面 log 重复的记录。保留当前图片和 screen 的 read_patch，首次输出才确认。旧无元数据的 log 原样显示。
- **超时兜底**：构造运行有硬超时（默认 45 秒），超时、出错或没交规格都按黑板加消息直接行动，并在注意里写明，绝不因上游卡住而挡住唤醒。
- **重放**：`construct.replay(ctx, policy_text=None, model=None)` 取一次封存构造的材料，带当前接口职责说明（旧样本会补上），可换一版 checklist 重跑，返回关注、引用、提醒、工具和接话条数的对照；不给 `agents__fork`、不跑行动。

### 面包屑（笔记名与别名）

`agent._prepare` 在每次行动组装好画面以后运行，不调模型：把画面文本和每份笔记的名字（`topic:线代` 取冒号后的 `线代`；`person:`、`group:` 的号码不算）以及笔记里 `别名：A、B` 行写的词做子串匹配（英文词两头不能紧挨别的字母数字，两个字以下不认），`global`、`recent`、`todo`、`awaker`、`observer` 不参与。命中、而那份笔记不在这次行动的材料里时，在请求末尾挂一行「画面提到的这些你有笔记，用 read_note 召回：…」。构造运行的输入里也有同样的一行。

### `#construct` 管理子命令

- `#construct`：查看开关、模型、思考等级、超时和说明来源；
- `#construct on|off`：开启或关闭构造运行（需管理员；关闭时每次都按黑板加消息兜底）；
- `#construct model [selection]`、`#construct effort [level]`、`#construct timeout [秒]`：查看或设置（带参数时需管理员）；
- `#construct prompt`：查看当前构造说明；
- `#construct set <全文>`：整份替换 `data/notes/policy/construct.md`（需管理员，留空回落内置说明）；
- `#construct reset`：恢复默认配置（需管理员）。

## 当前信任边界与维护取舍

工具调用不是由可信 Python 调用者选择，而是由外部模型根据聊天上下文和提示词生成参数。模型发起的工具调用属于 Bot 自己的行动：需要宿主级权限的入口统一读取固定的 `config.bot_permissions.op`，不借最后一个触发者或当前读到的消息作者。`BOT_OP_ONLY` 模块（目前只有 op 工具集）也按同一声明决定目录可见性和加载；其它模块仍对所有 LLM 轮显示，但其中需要 op 的函数会在调用时检查 Bot 自身权限。没有逐工具确认。

这使以下能力处于同一条提示注入路径上：

- `exec_code` 可访问 `.py loc`，本质上接近任意代码执行；
- `host` 的四个函数可以读、改、删宿主机任意路径的文件并执行任意 shell 命令，与 `exec_code` 同级；读取会把文件内容发送给模型供应商，写入和命令都不可撤销；
- `reload_tools` 可以在进程内执行并应用 `mods/tools` 中的受信任 Python；候选顶层代码在校验期间就会执行；`load_tools` 可以把任意 last-good 模块交给当前模型；
- `set_user_data` 可读取模型生成的 Python 字面量并修改任意用户 storage；
- `get_user_data` 可把任意用户数据发送给模型供应商；
- `later__later_add`／`later__later_del` 要求明确 `g`／`u` 目标；未来任务归属该目标窗口，任意代码按 Bot 自身权限判定，触发时不重新换身份；人类直接使用 `.later` 仍按消息作者判定；
- `fork` 可以增加并发、费用和工具调用深度；
- `common__poke` 要求明确目标窗口，会立即产生外部可见的戳一戳动作；
- `recognize_image` 可以下载模型指定的网络图片，或读取模型指定的本机 `file://` 绝对路径，再把图片及识别要求发送给视觉供应商，产生额外网络访问、宿主机文件读取和模型费用；
- `create_image` 会产生按张计费的外部 API 调用，返回可能过期的临时本地文件路径；
- `create_image_from_references` 还会下载模型指定的网络图片或读取本机 `file://` 绝对路径，并作为 multipart 文件上传给生图供应商；它同样只返回临时路径，发送须另用 `type_word`／`send` 明确目标；
- 天气和 MC 搜索会向外部站点发请求；`tts__speak` 会把要念的文字发给微软的在线朗读服务（`speech.platform.bing.com`）。

账户相关配置不进入源码或 storage。`main.py` 启动时加载仓库根的 `.env`：生图调用复用 `BYTECAT_BASE_URL` 并读取独立的 `BYTECAT_IMAGE_API_KEY`；天气调用分别读取 `QWEATHER_API_HOST`、`QWEATHER_KEY_ID`、`QWEATHER_PROJECT_ID` 和可选的 `QWEATHER_PRIVATE_KEY_FILE`。可从 [`.env.example`](../.env.example) 复制空白模板；缺少必需值时，调用会显式失败，不会回退到硬编码账户。

这是当前实现事实，但维护者当前接受这条信任模型：群聊中的 LLM 只应在手动选择的可信群启用，可信群中的聊天内容和外部模型共同处于可以影响 Bot 判断的范围内。按触发者切换权限挡不住混合上下文里的提示注入，却会让同一串工作因消息先后掉权限，所以这里选择一份固定 Bot 权限，不为每个工具增加确认、能力对象或独立沙箱。

这条策略有两个需要保持可见的边缘：

- 私聊当前没有 `chat_groups` 白名单，因此“只在可信群开启”并没有覆盖私聊入口；
- `config.bot_permissions.op=false` 会让 `exec_code`、`host`、本地文件读取、任意用户数据修改、任意代码延时任务和 Bot 身份命令拒绝执行，并隐藏 `BOT_OP_ONLY` 模块；它不会按窗口或消息作者变化。修改这项配置需要完整重启。
- 显式代行是另一类行为：`cmds__run_command(sender=X)` 构造作者为 X 的事件，后续普通命令按 X 的权限执行；Bot 自身没有 op 权限时拒绝代行人类 op，不能借 X 重新打开宿主能力。`message.recvmsg(sender_id=X)` 是可编程环境中的函数，不直接作为模型工具暴露，调用它的模型代码仍需经过 Bot 自身 op 门。

当前自动工具循环没有全局最大轮数、总预算或副作用回滚，`fork` 每次最多五项并发，也没有全局递归预算。维护者接受通过 `.reboot` 恢复极端失控调用的运维取舍，暂不据此引入完整预算系统；若真实发生无法靠重启方便恢复的事故，再以该事故为证据增加最小限制。

模块 reload 失败的完整 traceback 会作为结果发给模型供应商。维护者使用可信的正规供应商，并认为 traceback 对模型修正工具有实际价值，因此当前保留这一行为，不把内部路径随请求发送本身列为待修缺陷。普通工具异常只回传类型和错误文本。密钥、聊天正文或额外运行数据仍不应被无意加入异常文本。

值得保留的是：普通函数、签名和 docstring 组成一个可直接复用的能力面。未来若调整边界，应优先修改入口、启用范围或少量检查，而不是仅因工具锋利就建立庞大的工具对象宇宙。
