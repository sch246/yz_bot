# 中心闭环的隔离生产接线与联合验收

2026-10-21。实施基线 `6db9598a2977fbc7bb6fd22451d93258b8807fa5`，集成到当前 `codex/agent-memory-comparison-seams-20261020`。维护者对上一轮完整隔离实施授权问题回答“好啊”；该问题及候选范围见[决策包](production-entry-decision-20261021.md)。本 spec 只涵盖这个已批准的代码范围，不将 D12／D13／D14 整票视为完成。

## 可交付行为

在生产模块内接通一条实际可运行的中心路径，通过既有 `chat()` → agent → `llm.Chat` 推进。隔离范围通过显式装配进入这条路径，默认 live 装配不改变；同一个运行根只能有一个 agent arrival／activation／输出／行动权威。不得另挂实验 SyntheticHost，再把其结果称作生产中心接线。

验收用临时运行根、合成材料、假模型及假 OneBot；新路径必须实际替代对应旧责任，旧输入／通知／已读／output/result 写者不能在这一隔离运行根同时工作。当前默认旧路径因为真实部署和存量迁移尚未授权而暂留，不能静默回退；未来取消这段过渡的条件是独立切换审阅通过、存量责任可迁移及完整进程重启批准。

完整闭环是：按现有路由入档，区分普通到达与激活；冻结当前可见画面并按需取原文；每醒新上下文在同一中心选材、思考和显式行动；输出提交后才派发，结果与未知如实保存；主循环可主动修订并再读笔记；自主重访持久保存并兑现为新的激活；下一醒读取已提交事实、准确笔记版本和真实 delivery 身份。她可以选择不记、不重访、不说话，不强制轮末整理。

## 不变的范围

- 不读取 ignored 运行存量、真实聊天、配置、密钥、旧模型输出；不迁移真实材料。不调用收费模型或真实网络出口。
- 不对 live 5701 注入事件，不调用真实 QQ／主机控制，不启动当前工作树的真实 Bot，不重启或部署。允许仓库既有临时运行根 fake smoke。
- 修改都在主代理指定的独立 worktree，原主树无关改动保留；主代理审查接受 commit 后才集成。
- 不删除旧机制、历史或生产数据，不改已有 WHY 保护的事实，不代答 WHY?。必要的新维护者选择由 worker 报主代理，其余工作继续。
- 不恢复旧四臂比较，不追加真实模型行为采样；D12 反例与长期主体未证保持。

## 状态与证据

原归档持有各自格式的原文，不保证重建完整 OneBot 事件。唯一中心 journal 持有到达、激活、任期、实际请求曝光、输出、dispatch/result、确认／处置、重访和终态。pending 仅由激活减去准确确认／明确终止处置推导；旧 unread 成员与新 pending 不混同。

note 的不可变版本与当前 head 只由同一个受控入口提交。来源是实际已见归档片段、已提交自身输出和当时可确认的结果；自身理解不自动成为外部事实。主循环与可选后台共用 scope、截止、parent/hash 和持久故障规则；旧候选不换父版本强行覆盖。已冻结请求不回写，新读取才消费新版。

本轮入档先登记 writer 返回的 origin 与原文字节 hash，冻结画面时才按窗口／日取得 byte ref；不在每条到达或每次工具请求重扫日档。note 的父版本同时绑定实际请求中曝光的版本，不能拿旧请求换新 parent 提交。新中心只接当前 writer 的普通 v1；来源只读接口对 v0／sidecar 的支持不等于中心已经接通旧存量。

这是有限隔离根的接线：超出事件额度先拒绝请求，不用自动丢材料掩盖尚未实现的长期选材。日档仍整日物化，journal 仍全量恢复；这些不是已经解决的长期资源边界。

保留三种独立载体：长期 note、中心待办 hint、可加载 Skill／工具说明。保留工具 registry/last-good、load/reload、TTL 恢复、基础维护入口与现有权限。用户资料仍服务原业务，不自动转成她的立场。

每个模型请求记录实际传给 SDK 的 messages、工具快照及关联身份；仅 hash provider 之前的 session.messages 不能代表实际 wire。必须在转换图片等实际 payload 形成之后观察。禁止生产路径依赖 experiments 的 request-*.json 或其专用 client 协议。原 Chat 与整批工具语义复用，避免复制 LLM 工具循环。

request、输出与结果中的状态分清生成、提交、派发、完成、失败、未知。结果落盘失败不能导致副作用重放；共享故障门继续覆盖 llm／assign_tasks，agent mail 故障不禁掉已明确保留的维护路由。预算沿现有请求准入与结算边界，不把实验 100M 账本搬成生产预算；fake 验收必须绑定有限请求额度，实际成本为零。

## 来源接口：独立 worker 所有权

来源 worker 独占 `mods/chatlog.py` 和确有必要的 `mods/_backfill_archive.py`，不改实时 router、chat 初始化、中心循环、旧归档格式或现有有意修尾行为。亲读完整 owned 源码及 parser 必要依赖，新增显式严格只读接点：

```python
freeze_archive_day(*, root, kind, target, day, source_format,
                   bot_id, bot_names, names_complete, timezone)
read_archive_bytes(ref, *, root)
```

可新增明确的来源 namespace 参数；接口最终字段由 worker 提交前向主代理和中心 worker 固定。返回每条 `ref/raw/record`：ref 至少含窗口、day、source_format、origin、冻结 byte_start/end 与片段 sha256。需要单字符串 key 时只基于稳定定位和冻结片段，不含整文件 hash 或机器 root。记录 bytes、hash、边界和投影从同一份已读 bytes 得出。

新入口不调用 read_origin/read_range／索引／修尾／storage／身份 lookup，不隐式读取 Bot 身份和格式切线。可为原 parser 增可选参数保持默认旧行为；严格路径禁用私聊名字 lookup，timezone 必须实际控制时间转换。普通 v0/v1、private/group 与 sidecar 明确支持或拒绝，mixed day 无明确声明则拒绝，不能从相同头部猜版本。

严格路径仅 rb；不完整尾部、非法 UTF8/JSON/窗口／路径、不可确定边界拒绝，不备份／截断。规范相对定位与 root containment 防跨窗口和越界 symlink。读回只读固定旧 byte 范围并核长度/hash，追加不扩张引用；原范围改变、缩短或 hash 失配拒绝。v0 私聊 missing/guessed 不升级为完整事实。完整隐藏重复仍可定位，不用合并视图丢原件。

## 中心、笔记、重访接线：一个实现负责人

中心负责人拥有其他实际必要的生产文件，包括 `mods/chat/`、`mods/context.py`、`mods/llm/` 及明确的工具／reboot／connect/bot 接点。先亲读完整要改代码并固定最小方案；目录和 class 不预建为框架。可在自己的所有权内把不重叠的持久重访／恢复等切片交给最多两个孙代理，均须独立 worktree、明确接口，由其审查集成。不得改来源 worker 的两文件。

来源与中心可并行推进：中心先消费上面固定的 ref/raw/record 接口，用短小合成 stub 开发，但最终联合验收必须调用实际来源实现。主代理审查来源 commit 后给中心负责人明确可用基线；不自动 merge 主树或吞其他 worker 改动。

中心需接管的生产责任包括：

- 原 archive write 成功后才登记 arrival，激活依赖已登记到达；真实 message_sent 回声先归档后停止路由。原普通档 flush 与 sidecar fsync 保证不同，不虚构跨文件事务或统一断电保证。
- 唯一 owner／不可混淆任期，窗口锁内检查与首输出确认；输出只确认冻结覆盖的激活，新激活留下。取消／旧 finally 不伤新任期；waiter 优先、关联窗口 ^C/watchdog 和 owner 结束通知保持现有事实。
- 显式 say 复用原工具及假发送接点，只有实际 message ID 与 final_call 才结束，整批工具先完成。正文不默认外发；最终 #hint 路由固定原 origin、usage 等按实际请求与结束语义。
- 原工具 binding、TTL、schema、hint、image/reasoning 装配保留；不得只换 agent_rows 却让旧通知确认和安排阅读暗写旧 authority。旧公开 unread/cond 等接口须有明确兼容或明确不支持的结果，不能静默 noop 或双写。
- 同一 journal 下保存重访 request/due/cancel/delivery。scheduler 只有可重建安排，不持第二份“已兑现”权威；投递去重并与激活绑定，重启重建待投递，不把定时机会冒充主体自请重访。
- 保留现有通用 later；它执行表达式不等于中心重访。Bot 自重启恢复原窗口、人工重启不强开一轮、不伪造入站；用 fake supervisor 验证，不执行真实 reboot。
- 损坏／来源不匹配／持久化未知要停止模型及行动派发，保留已发生事实与维护观察入口，不自动修复或重放。取消是协作检查，不承诺中断在途调用。

实际未知的动态 .py/link/#hint 消费者、现场 .setname 注册与旧 pending/source/TTL 存量不能从静态代码证明兼容；本轮不读它们。无法在隔离范围完整接管的部分须明确拒绝并写切换前置，不以旁路旧 writer 假装支持。

## 验收与完成边界

仓库外有限验证脚本绑定 manifest：代码 commit/hash、合成输入和格式/窗口、临时根、无网络 transport、有限请求预算、初始 journal/note 状态、命令／资源与实际结果。不得把 fake 和实际 wire 形成前快照混称真实 SDK payload。保留第一次失败，不反复跑到通过后抹证据。

改前 check/smoke 基线；Python 改动后运行 `uv run --frozen python run.py --check`（可沿用专属已冻结 venv 和 offline/no-sync）；集成后隔离 smoke 对比 available/ctx 及可选失败。其余只验证改动和未决风险，不建永久测试套件。

联合证据至少覆盖：双窗口竞争／退出交界／取消；原过滤与回声；准确冻结确认；显式 say 目标／整批 final／固定 origin 收尾；主动 note 截止／CAS／同醒及后醒实际 wire；持久重访去重和新进程恢复；Bot／人工重启差异；TTL/hint/动态公共入口边界；归档、journal、派发后结果持久失败的未知及闭锁。来源反查覆盖追加后稳定、原片段变化拒绝、CRLF、重复原件、private/v0标签、sidecar残尾无修改及未知格式拒绝。

两个维度审查 Spec 与 Standards，主代理亲读 diff、关键代码与证据后集成。交付清楚列出接管／保留／不支持，旧责任在哪个隔离路径已停止、默认路径为什么仍暂留、将来删除条件。完成本票仅为隔离代码交付；不自动完成 D12，不允许真实切换、资料迁移、旧机制删除或付费运行。
