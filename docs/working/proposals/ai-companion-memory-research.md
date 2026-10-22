# ai-companion-kit 的记忆方案，与 QQ-agent 的比较

> 2026-10-19 公开源码调查。比较对象为 `pinganbbOvO/ai-companion-kit` 的 `24a9a0c6896b5bfe85d046c2ed245fd61cf9e090`（10 月 12 日）与 `K0nd1us/QQ-agent` 的 `fdfd325e148d91845a0aeea3dfdff4cf92fc61a1`（10 月 17 日）。只做静态核验，未运行两项目、未调用模型或 QQ、未访问柚子的运行数据。本文不批准改变柚子的记忆语义或部署。

结论：ai-companion-kit 的独立包使用按人保存的 JSON 账本，加上一条跨聊天场合的 JSONL 对话流，每次请求重建上下文；公开路径没有向量检索或知识图谱。它更明确地处理了“同一个人换个地方仍被认出”。QQ-agent 的公开核心按聊天窗口保存成员印象，提供模型写入、查询、删除和整理，印象维护的路径更完整。两者没有可用于比较长期记忆效果的共同实测。

本篇延续[QQ Bot 持久记忆实现调查](qqbot-persistent-memory-research.md)。复核确认，QQ-agent 当前版本的 `memory.js`、`tools.js`、`prompt.js`、`orchestrator.js`、`store.js`、`config.js` 与旧调查快照 `11577baad321d1774caf0765d791c211072bfa83` 内容相同。

## 独立 companion 包：保存与取用

[Memory 实现][ack-memory]在同一个数据目录保存两个文件：

| 载体 | 保存什么 | 默认边界 |
|---|---|---|
| `people.json` | 以用户 ID 为键的姓名、性格习惯 `traits`、事实 `facts`、话题 `topics`、未说出口的事 `pending`，以及互动计数和时间 | 四类数组每人分别最多 40、60、20、12 条；数组条目规范化空白后截至 160 个 JS 字符；完全相同文本挪到末尾 |
| `turns.jsonl` | 全局共享的对话轮次：时间、场合、显示名、输入和生成回复 | 最多 400 轮；输入和回复各截 300 个 JS 字符，压平空白；超量直接保留尾部 |

它们不是完整聊天档案。人物条目没有逐条消息来源、置信度或有效期；对话流的作者字段通常为显示名，缺失时回退用户 ID，没有独立的稳定人物 ID 字段。数组保留上限也不限制人物总数。[源码：字段与写入][ack-memory]

[Brain.reply][ack-brain]每次取当前人物的账本，再取最近 20 轮其他会话的对话（实现按会话 key 前缀排除当前来源）。账本实际只注入末尾的 traits 12 条、facts 12 条、topics 8 条、pending 4 条。这是按身份和时间选尾部，不按本轮问题进行语义检索。共享流没有再按人物或相关群成员过滤。

当前会话的历史另有 `history` 参数，最多带 20 条；但[实际入口][ack-entry]没有传它。因而默认路径排除了当前会话的共享流水，又没有补入该会话的消息历史；同一窗口此前的内容主要通过人物话题尾部延续。不能把“最近 20 轮”理解为默认保留当前窗口完整的最近 20 轮。

## 默认写入与演示的差别

最重要的实现边界在 [brain.js:79–90][ack-brain]：

- 成功生成回复后，自动写一条 `topic`，内容为输入前 40 个字符、箭头、回复前 40 个字符的拼接。它没有调用模型进行摘要或事实提炼。
- `traits`、`facts`、`pending` 有存储接口，但默认聊天流程不自动填入这些字段。
- `Brain.remember` 虽有“对应暴露给 AI 的 remember 工具”的注释，模型请求实际没有 `tools` 定义或工具调用循环；它只是可供外部代码调用的 JS 方法。
- [离线 demo][ack-demo]直接预填人物事实和习惯，再演示读取；它证明同一 ID 可以跨场合读同一本账，不能证明聊天模型自动学会了这些事实。

因此，“尝试记录生成轮次”和“会提炼值得长期记住的内容”在这里是两个不同事实。现版独立包默认在模型请求成功返回后执行记录，也提供事实提炼所需的部分存储接口。请求失败不记录，调用者可用 `record:false` 关闭记录；流水写入失败会被吞掉，并不保证成功落盘。

[入口][ack-entry]还有几项会影响记忆语义：没有唤醒且不是 owner 的消息直接退出，不会成为记忆；群聊批次只向 Brain 传最后发言者的 ID，多人内容会写进最后那人的话题；对话记录在 QQ 发送前写入，dry-run 或发送失败也可能留下生成的回复。因此其“真实对话流”不能充当发送成功的证据。以上来自调用顺序，未通过运行复验。

## 和 QQ-agent 公开核心的对应差异

| 比较项 | ai-companion-kit 独立包 | QQ-agent 公开核心 |
|---|---|---|
| 记忆归属 | 同一用户跨群和私聊共用账本 | 聊天窗口下再按成员保存；同一人在不同窗口不自动共用 |
| 日常写入 | 默认在模型请求成功后机械截取输入、输出存入话题 | 模型调用 `memory_append` 追加稳定印象 |
| 自动取用 | 当前人物账本尾部，加其他会话最近 20 轮 | 按触发者、历史窗口或锚点的成员集合精确筛选，每人末 3 条印象 |
| 历史来源 | 有界、截短的生成轮次流；未唤醒消息不入流 | 入站即保存按会话的消息记录，默认无条数上限，可用历史工具翻阅 |
| 修订与遗忘 | 字符串全等去重、FIFO 丢旧条目；可手改文件 | 查询、删除、手动编辑／替换，加模型整理；成员替换保留最近一次备份 |
| 语义检索 | 没有 | 所比较的公开成员印象路径也没有 |

QQ-agent 证据：[目录与成员文件][qq-memory]、[注入筛选][qq-prompt]、[每人末 3 条][qq-slice]、[记忆工具][qq-tools]、[入站存档][qq-store]、[历史工具][qq-history]。

QQ-agent 的整理默认在聊天流程后检查：总印象数大于 4，或某人超过 5 条，再满足默认 6 小时冷却，才安排模型合并／删除；已有印象的整理结果如果条数增加，会放弃。默认整理后每人最多 5 条、每条 120 个 JS 字符。这些规则限制输出形状，不能证明语义正确。[触发][qq-trigger]、[整理执行][qq-consolidate]

它也有写入不足的问题。源码记录日常模型很少调用 `memory_append`，甚至存在聊了 200 多条仍零印象的情况，于是批量整理增加了从活跃成员近期发言补提炼印象的路径。但是完全零印象的会话仍过不了自动整理的数量门槛，需要手动整理或先产生印象。不能把“有补录实现”写成“任何新群都能自动补齐”。[失败说明与执行入口][qq-consolidate]、[自动触发门槛][qq-trigger]

QQ-agent 文档还描述了 conversation-memory、knowledge-memes、memory-recall 等扩展；当前公开仓库没有对应实现或 manifest，不能用这些名字推断其召回算法、默认能力或与独立包的效果差距。[扩展文档][qq-extensions]；更多边界见[既有调查](qqbot-persistent-memory-research.md)。

## 教程和 qq-bridge 补丁另有一套更丰富的组合

仓库展示的陪伴体验还包括人物账本、共享流水、未完成的话题／想法、日记及图库，不能全部归到 `packages/companion` 上。

[完整教程][ack-tutorial]描述 `state/people.json`、`state/shared-chat.jsonl`，以及 `qq_memory_append` 的 activeTopic、pendingThought、memberImpression 分类。人物账本跨场合共享；日记另由模型用写入／读取工具维护，并在后续提示词中再次出现。

[桥接补丁][ack-patch]给出了部分具体接线：

- `rememberPerson` 存入人物账本；`memberImpression` 同步为 trait，`activeTopic` 同步为 topic。所查新增接线没有自动 fact 提炼调用。
- `formatPeopleBlockV2` 在私聊注入对端账本，在群聊注入 owner 的账本；它并非独立包“当前发言者”的同一选择规则。
- 唤醒提示词另加共享流水最近 20 条、日记最近 3 条、图库最近 4 条，以及其他会话的少量近况。
- 补丁提供写日记、读日记和按日翻实际聊天材料的接口，也有定时唤醒模型、由模型选择是否写日记的接线。

但共享流水、日记和按日读取聊天的实现通过作者机器上的绝对路径导入，对应模块未随当前仓库提供。因此只能确认补丁的调用接口，无法完整核对那些模块的存储、归档、纠错和召回行为。进阶教程要求定期重写印象，也不能视为独立包已有自动整理机制。

[一键安装脚本][ack-install]实际下载另一个仓库 `Derpyu520/qq-bridge`，没有应用本仓库补丁的步骤；也不能由安装入口推断用户已经获得作者展示的整套记忆组合。本次未追查第三个仓库的当前实现或部署组合。

## 对后续研究有用的判断

ai-companion-kit 可借鉴的是明确让人物身份跨场合延续，并让模型自己留下的日记在以后再次参与思考。QQ-agent 可借鉴的是把印象写入、查询、修订和整理接成可执行路径，同时保留它已经观测到的漏写失败。

从这些代码还推不出谁更会记事。需要分别观察：过去是否留下记录、记录是否正确归属、下一次是否真的取用、旧判断能否被修正。尤其不能把预填 demo、字段名称或“纯文本可编辑”当成自动学习和长期正确性的证明。

[ack-memory]: https://github.com/pinganbbOvO/ai-companion-kit/blob/24a9a0c6896b5bfe85d046c2ed245fd61cf9e090/packages/companion/src/memory.js#L31
[ack-brain]: https://github.com/pinganbbOvO/ai-companion-kit/blob/24a9a0c6896b5bfe85d046c2ed245fd61cf9e090/packages/companion/src/brain.js#L47
[ack-entry]: https://github.com/pinganbbOvO/ai-companion-kit/blob/24a9a0c6896b5bfe85d046c2ed245fd61cf9e090/packages/companion/src/index.js#L128
[ack-demo]: https://github.com/pinganbbOvO/ai-companion-kit/blob/24a9a0c6896b5bfe85d046c2ed245fd61cf9e090/packages/companion/examples/demo.js#L24
[ack-tutorial]: https://github.com/pinganbbOvO/ai-companion-kit/blob/24a9a0c6896b5bfe85d046c2ed245fd61cf9e090/docs/%E5%AE%8C%E6%95%B4%E6%95%99%E7%A8%8B-%E4%BB%8E%E9%9B%B6%E6%90%AD%E4%B8%80%E5%8F%AA%E5%B0%8F%E9%B2%B8%E9%B1%BC.md#L305
[ack-patch]: https://github.com/pinganbbOvO/ai-companion-kit/blob/24a9a0c6896b5bfe85d046c2ed245fd61cf9e090/patches/01-bridge.patch#L1046
[ack-install]: https://github.com/pinganbbOvO/ai-companion-kit/blob/24a9a0c6896b5bfe85d046c2ed245fd61cf9e090/tools/%E5%AE%89%E8%A3%85.bat#L90
[qq-memory]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/memory.js#L1
[qq-prompt]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L806
[qq-slice]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/memory.js#L455
[qq-tools]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/tools.js#L649
[qq-store]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/store.js#L1
[qq-history]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/tools.js#L469
[qq-trigger]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/orchestrator.js#L1444
[qq-consolidate]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/orchestrator.js#L1720
[qq-extensions]: https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/doc/extend_development/prompt-pipeline.md#L94
