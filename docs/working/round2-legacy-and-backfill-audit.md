# 第二轮：旧兼容与离线补回调查

> **只读调查，未实施迁移，未验收真实停机空窗。** 本文对应[第二轮计划第 7 节](proposals/agent-simplify-round2.md#7-旧兼容分支和离线补回先检查后决定)。现行用户语义以[交互模型](../interaction-model.md)为准；本文的三选一是建议，不是已获授权的生产数据操作。第一版只读版本控制内的代码和文档；2026-09-27 经维护者授权补做了脱敏的生产数据只读复核，仍未查询 NapCat 或改写任何运行时状态。

## 2026-09-27 生产数据只读复核

维护者随后授权检查现有数据，但没有授权转换或删除。本次只固定每个文件当时已有的完整前缀，统计格式和字段；没有导出正文、身份、窗口号、密钥或私有端点，也没有修改生产文件。运行中的 Bot 进程启动于 2026-09-26，早于第二轮代码部署；扫描期间事件流仍从 8244 行增长到 8246 行。因此下面是活数据的只读快照，任何“当前为零”的旧形状都必须在停机后的最终冻结中复核，不能据此在线删兼容。

### 实际存在的旧形状

- `data/event_stream` 有 4 个 journal、8246 条完整记录；当前重放器逐行校验全部通过，无完整行 JSON 错误和残尾。旧形状包括：124 条 `condensed`、379 条 `body/actions` output、33 条旧 notification、94 条 `projection=None` input、21 条无 `read_by` 的 `mark_read`、15 条没有 `positions/read_by` 的旧 `source_mark_read`。1752 条 input 中只有 4 条带新 `read_by/read_via`。
- 596 条历史 arrival 带 `_stream_results`，但按完整日志重放后待消费数为 0；这只能说明桥已经排空当时的遗留，不能阻止尚未重启的旧进程再次追加。
- 925 条 `source_start` 全都有 `queue_window` 和 `pending_boundary`；15 条 `source_mark_read` 全都有 `mention_count`。`floor`、`clear`、`start` 均为 0。由此可在停机终检后收紧这些字段或删除零数据分支，但 `condensed`、旧 source 坐标恢复和旧 output 兼容仍然承重。
- 33 条 notification 全部已确认：22 条没有 `activations`，11 条有 `activations` 但没有 `version=2`；尚无新版 v2 通知。不能拿当前未读状态回填旧快照。
- 当前持久 input 尚无工具状态事件，也没有冻结的 `<sent_by>`。旧记录缺少足够的明确目标事实，不能事后猜写 say／回声关系。
- 旧 `data/storage/oplog` 还有 5 个文件、482 条 `opN` 记录，其中 168 条已收缩；当前源码已无读取者。3 个文件的编号存在历史空洞，不能按剩余条数重建 cid。它们是可归档或删除的退休轨道，不是可并入统一事件流的数据。
- 旧窗口聊天设置只出现在 4 个群 storage 和 1 个私聊 storage；其中 2 个文件同时保留仍有效的 `hint`。可删除键仅限 `model/image/reasoning/tools/max_events/max_msg/max_token/pressure_percent/prompt/active_tools`，不能整文件删除，也不能把多窗口值自动并入全局 agent。
- 全局 `agent` 当前没有 `active_tools`；所有仍存在的窗口 `active_tools` 已是时间戳字典，不存在待转换的名字列表。动态兼容符号扫描在 `pyload`、群／用户设置与 hint 中未找到 `chat.cond`／`chat.call` 消费者；命中只来自退休的旧 oplog 正文，不能据此反向修改历史。
- 普通 chatlog 仍以 v0 为绝对主体：8408 个 v0 日档、122 个 v1 日档和 7 个切换日档。v0 原始 CQ 与显示文字已经不可区分，继续只读适配，不做伪升级。
- 175 个 backfill sidecar 共 1545 行，全部是完整合法 JSON；182 个 source page 与 journal 一一对应，成员数一致，无孤页、缺页、pending 或临时页。88 个派生 SQLite 索引全部通过 `integrity_check`，字段也已经是当前 schema；无需删除重建。

### 据此采用的三类处理

1. **保留兼容层：** v0 chatlog、`condensed`、旧 notification、未知 read provenance、旧 `source_mark_read` 坐标恢复、旧 output、`projection=None` 和旧窗口正式号都由当前 replay／projection 适配；不改写原日志，也不补造因果关系。
2. **格式升级：** 新代码只写当前 notification、工具状态、read provenance、source 与 output 形状；生产重启后用真实新样本确认。现有派生索引已经是当前 schema，不做无意义重建。`_stream_results` 只让兼容桥排空，不把旧 arrival 重写成另一套历史。
3. **删除候选：** 旧窗口聊天键和退休的 `data/storage/oplog` 需要停机、备份与维护者明确授权后处理；`floor/clear`、`source_start.queue_window` fallback、全局 `active_tools` 列表兼容、旧 reboot payload 与 `_stream_results` 桥，都要在新代码部署、成功重启并再次冻结计数后才能删。删除代码兼容不需要改历史，但不能在旧进程仍写入时提前宣布完成。

这次没有执行任何不可逆操作。若获准清理，先停止 Bot 并备份相关 storage 与 event stream；旧窗口设置按精确键删除，退休 oplog 按整目录归档后再从运行路径移除。事件流、source pages、chatlog 原文、正式号和引用不进入清理范围。

## 旧兼容分支清单

建议的共同边界：日志原字节、正式号、来源定位及引用关系是事实，不靠重算或重写来“统一”。若采用适配层，应在持久记录进入主重放语义前集中解释旧形状；新写入只产当前形状，不让各个投影和工具继续各自猜旧格式。下表的“删除”只表示在维护者确认无消费者且授权后删旧**职责**或旧**数据**，不是这次调查已经删除。

| 位置与旧形状 | 保护的数据／现行作用 | 建议与理由、删除条件 |
|---|---|---|
| `mods/oplog/replay.py:180`, `:197`, `:310`, `:352`, `:364`, `:391`；`mods/oplog/__init__.py:792`, `:914` 的 `floor`、`condensed`、`clear` | `floor` 按旧到达边界移除未读，`condensed` 隐藏旧事件及其结果，`clear` 隐藏旧结果；新代码不再写这些行。删掉分支会使旧事件复现，或使完整日志无法重放。 | **适配层**，倾向重放入口集中解释为当前未读／可见性状态，原 JSONL 不改。不能机械改写成 `mark_read`／`cover`：它们有不同的前缀、行动号和引用约束。删层条件是备份后的离线重放证明所有生产日志不含这些行，或维护者明确授权数据转换并证明号与引用不变。 |
| `mods/chat/view.py:160`、`mods/oplog/__init__.py:554` 附近的旧 `notification`：无 `activations`、无 `ordinal`、旧 `unread` 快照 | 已编号通知须能重建；通知投影在缺 `activations` 时写“旧版通知”，未读摘要使用已有快照，`_unread_detail_text` 对缺 `ordinal` 容忍。未确认通知还要重投。 | **适配层**；第 6 节切换新通知写法时保留旧通知读取，集中把旧字段转成可投影形状，不按当前未读改写旧通知。旧快照是当时事实，不能拿当前 `status` 回填。 |
| `mods/oplog/replay.py:126`、`mods/chat/reader.py:573`, `:609`：旧 input 无 `read_by`／`read_via`；旧 `mark_read`／`source_mark_read` 无发起号 | 正式 input 号与正文仍有效；旧跳过桥没有可证明的行动号，现显示 `skipped_by=unknown`；`event_links.reads` 只能从确有的 `read_by` 派生。 | **适配层**，规范化为“来源未知”，不得按相邻输出推测或伪造关联。主投影只认规范化来源；日志仍保留缺省这个历史事实。 |
| `mods/chat/reader.py:116`、`mods/chat/agent.py:163`，`mods/oplog/__init__.py:485`：`_stream_results` 到达 | 上一版已经把工具结果放进 agent 未读队列；下一次激活桥接成普通 R，避免丢掉已完成工具批次。新结果已直接写 R。 | **适配层（短期）**，只在启动／重放边界排空遗留到达，不恢复第二条日常结果路径。须在获授权的生产只读检查确认没有待排空到达、部署并重启后再删；仅凭本地代码或 smoke 不能宣布无残留。 |
| `mods/oplog/__init__.py:617`, `:664`, `:792`、`mods/chat/view.py:261`：旧窗口正式事件及 `projection=None` 的 input；`mods/chat/view.py:398`, `:407`, `:428`, `:506` 的 `get_msgs`／`_chat_msgs`／`build_context` | 旧事件号可由中心 `AGENT_WINDOW` 反查，`projection=None` 不伪装成模型确实读过的正文；窗口视图仍供独立 `.chat`、`#add_prompt` 和 `context_usage` 兜底。 | **适配层 + 删除旧视图职责**。保留全局号索引与反查，不重写窗口事件；第 2 节删 `.chat` 后，经设备动态调用调查确认再删窗口上下文视图。`projection=None` 的缺失不能凭原 event 杜撰为当时投影。 |
| `mods/oplog/replay.py:256`, `:94`、`mods/oplog/__init__.py:398`：旧 `source_start` 缺 `queue_window`、旧 `source_mark_read` 只有 `page`／`offset`／`count` 而无 `positions`；另有 `mention_count` 缺省 | 重放时旧信源沿用原窗口，旧 mark-read 由当时未读前缀恢复坐标；已跳过位置与提及计数不可丢。 | **适配层**。把旧记录转成明确队列位置／坐标再交给现行校验与派生；不可用当前未读前缀重算，必须保持日志顺序下的状态。确认生产日志不含对应旧形状后可删。 |
| `mods/oplog/replay.py:375`、`mods/chat/view.py:230`、`mods/oplog/__init__.py:766` 的旧 output `body`／`actions` 与中心新 `assistant`／原生配对 | 老输出和结果的正式号、工具位置及可反查内容；中心新格式保存供应商原生 assistant。独立 `.chat` 在第 2 节删除前仍写 `body`／`actions`，不能提前宣称它只是历史格式。 | **适配层**，先随 `.chat` 删除停掉旧形状的新写入，再在重放／投影入口集中产生当前内部 output 形状。原生配对仅在旧数据确有足够字段时使用；不能给旧行动编造 provider tool-call id。旧事件仍存在时不能直接删文字投影。 |
| `mods/chat/__init__.py:462`, `:464`、`mods/tools/__init__.py:1101`：`active_tools` 的纯名字列表 | 旧激活名单没有使用时间；现把每项当“刚用过”，防止一重启就被一小时闲置规则收回。全局与窗口 storage 都可能有旧值。 | **转换**到 `{模块名: 使用时刻}`，但转换时刻会改变 TTL 语义，须维护者确认；备份并停机后按同一固定时间转换。若第 2 节删 `.chat`，窗口激活名单应按下行删除，而非迁入全局。 |
| `mods/chat/__init__.py:108`、`mods/chat/subcommands.py:245` 的 `max_msg`，及 `getchatstorage`／`window_setting`／`WINDOW_SETTINGS` 中 `.chat` 的 `model`、`image`、`reasoning`、`tools`、`max_events`、`max_token`、`pressure_percent`、`prompt`、`active_tools` | 旧窗口会话设置目前仍决定 `.chat`；`max_msg` 只在 `max_events` 缺失时兜底。全局 agent 已有独立设置。 | 第 2 节语义定为**删除**窗口 `.chat` 设置，绝不自动并入全局。停机、备份、清点并获明确删除授权后清理旧键；运行代码同时停止读取。`hint` 和 `agent_hint` 仍是窗口数据，不能误删。 |
| `mods/chat/__init__.py:683`, `:695` 的 `chat.cond()`／`chat.call()` | 公开 Python 名字可能被动态 link、pyload 或 hint 运行时代码调用；仓库 grep 无法证明设备数据没有消费者。 | **待决：适配层或删除**。先获授权只读检查三处设备代码及其它动态消费者。若仍被用，给短期迁移入口并逐一改调用；若无人用，删旧名字而不保留永久别名。不能靠源码内零引用直接删。 |
| `mods/chatlog.py:605`, `:775` 的 v0/v1 解析、`_day_version`、`_guess_private_sender` | 旧私聊作者可能只能按历史显示名推测，旧正文只是显示投影；原行无法无损升级。 | **已裁决的适配层，不纳入清除队列**：[chatlog 格式协议](../chatlog-format.md#v0切换之前的行)规定旧档只读、不重写。检查结果：主阅读接口仍经 `parse_log`／`read_range`；`_restore_window` 明确不把 v0 还原为可信实时内存。 |

这不是“凡是默认值都是兼容”的清单：`read_by=None` 也用于无工具发起的正常路径；`queue_window` 和 `mark_read.positions` 的旧形状是否真的存在于生产，需要获授权的只读计数才能定。`shutdown` 的所谓 legacy greeting 解析仍对应当前写入格式，不应误删为过渡分支（`mods/shutdown.py:16`, `:60`）。

### 生产数据决策闸门

1. **只读摸底也需授权。** 由维护者指定可读范围和脱敏输出，仅统计事件种类／缺失字段／窗口设置键／动态调用符号，不导出正文、账号、群号、路径中的私密标识或密钥。确认当前生产进程版本与待部署代码是否一致。
2. **先选每项去向。** 尤其决定 oplog 适配层的集中位置与生命周期、`active_tools` 列表的 TTL 转换时刻、动态公开名是否有人用、旧窗口设置可否删除。未获决策不修改生产文件。
3. **转换／删除须单独明确授权。** 停止 Bot 及 storage 同步，完整备份关联 storage、event_stream 与 chatlog，记录恢复命令和可逆范围；只在离线副本试转换、重放全量 JSONL，再比对正式号集合、事件引用、未读／已读／跳过位置和可见投影。原子替换并再次核验后才启动；失败回滚整组相关文件。不要在线编辑 oplog，也不要把原号重新编号。

## 离线补回：现行规则与保护场景

这是**当前代码的静态调查**，不是对 NapCat 离线历史完整性的证明。起点是 `mods/connect.py:199` 在监听器开放前调用 `chat.prepare_recovery_sources`；`mods/chat/reader.py:135` 从已有本地窗口冻结锚点和 pending 边界，`mods/chat/__init__.py:817` 在启动完成后用最多两个后台 worker 补回；实时入站仍由 `mods/bot.py:128` 的普通日志／arrival 路径接收。自动补回只覆盖 `chatlog.known_windows()` 已有的窗口（`mods/chatlog.py:1389`），新出现的窗口没有自动发现承诺。

| 现行规则与代码 | 它保护的情况／不可误称的保证 |
|---|---|
| `mods/chatlog.py:1400`, `:1433`：取启动前最近可靠 `message_id`，损坏窗口降为无锚点；`mods/_backfill.py:111` 无锚点自动启动只取最近一页。 | 开机后的实时消息不能移动锚点；没有可靠锚点时不把漫游旧史自动灌满未读。单页是首次边界，不代表上游尽头或无缺。 |
| `mods/_napcat_history.py:30`：按群／私聊历史 action 请求，`message_seq` 倒向分页、剔除重复游标；非空短页继续，只有命中冻结锚点或空页才正常停止，接口／字段／游标错误抛 `HistoryGap`。 | 保护多页空窗和边界停滞；请求成功、短页、当前 chatlog 新增消息都不能假装“追到了”。命中锚点只证明请求链抵达，不证明上游逐条无漏。 |
| `mods/_source_pages.py:93`, `:134`、`mods/_backfill.py:64`：远端原始页先持久暂存，再写 sidecar 原文、不可变引用页，最后发布 `source_page` journal；重启补提交孤页／pending 页。 | 跨档案与日志两份文件的崩溃夹缝可重试，不靠游标重取可能变化的正文；相异重试拒绝覆盖。每页有上限，但全来源页数和索引规模仍可增长，不等于总内存或磁盘有界。 |
| `mods/_backfill_archive.py:255`, `:428`：按窗口、消息号、时间、远端序号重用 sidecar origin，不向普通 `DD.log` 插行；范围阅读按时间和序号合并。 | 保护已有 `_log_origin` 行号和 `read_origin`／`before` 定位；普通日档无 `message_seq`，不能按正文或秒级时间强行去重。允许不确定时保留重复，不能把重复隐藏成“完整”。 |
| `mods/_backfill.py:17`, `:76`：同窗口先前 boot source 的 origin 暂建索引；源开始时保存的 `pending_boundary` 之前已有 live arrival 不再列入补回页；窗口锁包住入列。 | 避免重复开机或实时／补回重叠在同一个窗口信源重复排队；保留旧待读前缀。跨信源 `fetch` 的重叠刻意仍可见，不能全局去重。 |
| `mods/chat/reader.py:507`, `:632`：窗口成员顺序按旧 arrival、各 boot source 倒页正行、再实时尾部重建；`_iter_unread_metadata` 和 `mark_window_read` 拒绝未收束的补回。 | 补回在开机后实时消息之前、旧待读之后，且不倒插已正式阅读的经历；补回进行时显示“补回中”而非误报为空。`take` 按选择在下一 provider 请求前才逐条成为正式 input。 |
| `mods/chat/reader.py:165`, `:301`, `:632`：`fetch` 对未读 gap／failed 来源可续接，对已拉取或已完成来源另建 `napcat_history`；`status`／hint 展示缺口和状态。 | 已消费的信源不被向前插页；分页失败保留已取得原文与未闭合事实，不能以新查询静默抹掉旧缺口。 |
| `mods/bot.py:130`、`mods/chat/__init__.py:607` 的 `record_event`：补回只归档并进入待读，不再走实时命令、link、唤醒分派。 | 防止停机期间的一条命令或宽 link 在重新开机时执行副作用。戳一戳／撤回等 notice 不保证能由消息历史 API 补回。 |

### 可简化选项与代价

| 选择 | 可删的责任 | 必须付出的产品／数据代价 |
|---|---|---|
| **保留现行窗口内补回，局部收束实现**（低语义风险） | 可审计重复扫描与状态派生，复用同一页／同一窗口的轻量索引；不能删 `pending_boundary`／`arrival_boundary` 及读前屏障。 | 仍有多文件提交和窗口顺序计算的认知成本；必须先用真实停机空窗证明它值得保留。不要为追求行数少而去掉失败状态。 |
| **启动补回也做成独立 `fetch` 信源**（最大结构简化） | 可去掉窗口内前插、旧 arrival 与 boot 页的交错、窗口 `take`／`mark_read` 屏障及相应边界计算；一来源一队列。 | **改变已拍板的交互语义**：停机消息不再是原窗口未读前缀的一部分；模型必须主动发现并读取另一个 key，窗口 `status`／提及序号、桥接及 `mark_read(g/u)` 覆盖面都会变化。实时消息可能先被读到，而旧消息后来才读，违反当前时间顺序。旧 boot source 如何迁移／展示还需决定。不能把它称为等价内部重构。 |
| **只恢复档案、不自动入未读** | 可删 source 页、读前屏障及自动待读状态。 | 停机消息永不自动进入中心 reader，只有人工／模型显式档案查询才能发现；与“离线经历可读”目标冲突。 |
| **按固定页数／时间截断追溯** | 避免长空窗持续分页与较大容量。 | 人为制造未知缺口；必须明确标 gap、可续接，并由维护者批准放弃“可靠锚点要追到锚点或真实失败”的现行规则，不能用成功状态掩盖。 |

目前最有价值的先手不是改成独立信源，而是**先真实验收**：当前窗口内合并是明确产品决定，独立信源会把复杂度转嫁给使用者，并非无代价删除。

### 真实停机验收方案（尚未执行）

1. **授权与保护。** 维护者明确批准停机／启动、只读访问生产 chatlog／event_stream／状态、只读 NapCat 历史 API 查询，以及真实参与者在指定窗口发验收消息；指定时间窗、窗口类别、备份／回滚负责人和可接受停机时长。不得向运行中的 `5701` POST 合成事件，不借 Bot 自身向 QQ 发测试消息，未经授权不操作设备。先备份关联档案、source pages、event_stream 和 storage；测试记录只留脱敏计数、相对顺序与状态，不写正文或身份。
2. **停前基线。** 在授权窗口对一个既有群聊和一个既有私聊分别记录最后可靠本地锚点是否存在、未读成员数量与顺序、`status` 和未确认通知状态；另选一个无可靠锚点的已知窗口（若自然存在）。只读查询确认当前 NapCat 版本的群／私聊分页方向、返回字段和上游是否可见停机期消息，不把接口可用当作覆盖证明。
3. **真实空窗。** 维护者正常停机，真实参与者在两个已知窗口各发送按时间可辨的普通消息；其中安排跨两页以上的样本和一条可能与重启实时阶段重叠的消息，必要时另做一次受控异常中断。所有消息必须由授权的人真实发送，不伪造 OneBot 入站。若无法安全制造多页，只记录这一项未验收，不冒称通过。
4. **重启与并发。** 维护者启动；补回未结束时同窗口再来真实普通消息，观察该窗口 `take`／`mark_read` 被屏障拒绝而其他信源和实时入站仍工作。补回收束后核对：旧待读前缀 → 停机消息旧到新 → 重启实时尾部；群／私聊作者、时间、原始 CQ 结构及 Bot 自发回声（若有真实样本）与只读上游相符，且无命令／link 副作用重放。
5. **持久与失败边界。** 只读比较普通日档未被插行、sidecar origin 可 `read_origin`、不可变 source 页与 journal 页数相符；重启一次确认不重复入列、正式号／引用不变。另用隔离的临时假 API（不接生产实例）核验短页继续、锚点重叠、游标停滞、中途失败与重试；真实设备若恰好遇到 API 失败，只观察 `gap` 和续接，不故意破坏生产网络。已取得部分必须可见，缺口不得被报为 complete。
6. **结论标准。** 逐窗口记录“锚点命中／上游尽头／gap／无锚点首批”、远端实际可见数与本地归档／未读接纳数、重复与缺失数、状态和顺序；分别报告代码机制是否按规则工作、NapCat 是否覆盖该次停机空窗。不能从一次成功推广到所有历史类型、所有窗口或上游绝无漏页。

未解决的维护者选择：是否允许把启动补回改为独立信源并接受顺序语义变化；自动发现全新窗口是否属于目标；异常中断验收是否值得生产风险；离线补回长期容量上限如何取舍。上述选择与旧兼容清单的生产摸底／转换授权彼此独立，不能用这份静态调查代替批准。
