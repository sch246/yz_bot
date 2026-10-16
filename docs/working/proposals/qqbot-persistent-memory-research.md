# QQ Bot 持久记忆：论文、视频项目与宿主机制

> 2026-10-14 的公开资料与源码调查；不是运行合同，不批准部署或改变柚子的记忆语义。论文总图见[记忆与自我演化研究地图](memory-and-self-evolution-research.md)。

## arXiv:2610.07625 是否已经收录

已经存在于研究地图的「状态与上下文分离」一节、问题矩阵和实验对照项。调查开始时它属于工作区尚未提交的改动；当时的 `HEAD` 尚无论文编号。收录研究与在 Bot 中实现 SLA 是两回事，不能由文档条目推断运行进程已经采用论文方案。

[SLA 全文 v1](https://arxiv.org/html/2610.07625v1)最值得借鉴的部分是：跨调用证据留在 harness，每次按角色和任务重建上下文，并由规划者把已测结果变成具体下一实验。完整实验记录仍保存，角色看到的是有选择的证据。现有研究地图已补正完整状态的范围、token 与费用区别，以及消融未单独隔离跨调用对话保留的限制；[作者仓库](https://github.com/Alex-q-z/stateless-language-agents)在本次查看时只有 assets，没有可运行框架代码。

### SLA 怎样管理跨调用记忆，与 QQ-agent 的区别

2026-10-14 补核正文 §3.1–3.3、附录 B.1 与 [PDF 附录 E](https://arxiv.org/pdf/2610.07625v1#page=22)。HTML 的 E 节没有显示提示词正文，不能把阅读 HTML 等同完整读过附录。

SLA 的跨调用记忆在 harness 保存的研究状态中：针对当前研究任务的候选代码、独立评测结果、完整实验顺序与运行状态，并做 checkpoint。每次调用都从这些状态重建上下文；agent 会话不跨调用继承，不能把会话清空当作研究记忆的遗忘。Advisor 每个 epoch 得到全局最好代码和分数、近期尝试、按方向归组的结果、Worker 状态与任务诊断；Worker 每个 trial 只得到任务、指派、自身保留候选和局部反馈，不能自由翻完整研究档案。记录层保留与本轮可见上下文选择是两件事。

附录 E 将证据使用约束写得更细：kernel Advisor 得到经清理的指标账本与汇总分析，不能读取 Worker 对话、被拒绝候选的原始代码或隐藏评测细节。有效实验没有改善是负面证据；实现、运行或正确性失败对整个方向仍不足以下结论。SOL 示例用 REGIME 标签区分方法路线，让年轻路线先与自身历史比较，不因暂时落后全局最好就淘汰。方向未变时保留局部改进；转向时从全局最好重新开始，首个成功评测的候选建立新局部基线；失败／丢弃尝试恢复保留候选。这里有明确状态更新规则，不能概括为自动总结所有聊天。

两者共同之处是外部状态持续、每次新建模型会话；区别是 SLA 围绕已知评价目标组织实验事实、候选与角色可见性，QQ-agent 公开核心围绕聊天窗口和成员组织原始消息与短印象，并按参与者注入、由模型工具和整理入口更新印象。SLA 的代码候选本身就携带可执行的进展，QQ-agent 的成员印象更多是对经历的语言解释。前者以外部测量约束候选保留，后者没有同等的关系／人格事实判定器。

论文没有披露可复现的完整摘要构造算法、账本全部字段和存储实现，也未提供通用遗忘、事实矛盾消解或跨任务语义检索方案。QQ-agent 的未公开分层记忆扩展同样不能做算法级比较。可据公开材料比较责任、载体、选择与更新规则，不能宣称任一方解决了通用长期记忆。

### JitRL、SLA 与 QQ-agent：先定位记忆，再比较改进、遗忘与检索

本节以整个运行系统为比较边界，沿用[基础讨论](memory-foundations-discussion.md#二记忆是过去影响现在的方式)中“过去经历影响现在行为”的功能定义。先问运行经历留下哪些跨调用状态，再问这些状态通过什么路径参与后续行为；不能把一轮 LLM 的会话当成整个系统的记忆。文件存在只证明痕迹保存，实际被取用和影响行为才证明相应记忆功能发生过。本节不比较模型预训练获得的先验，也不把某种持久数据结构预定为唯一记忆单位。

| 比较项 | JitRL | SLA | QQ-agent 公开核心 |
|---|---|---|---|
| 跨调用状态在哪里 | 经验库、逐步状态／动作／后续奖励序列及检索索引；论文将其概括为 `(s,a,G)` | harness 的候选快照、评测结果、完整实验档案与运行状态 | 每个聊天窗口的消息 JSON，以及按成员保存的印象文件 |
| 过去怎样影响现在 | 相似经验的回报统计改变候选动作的选择分数；不只依靠口述经验 | 保留候选携带已取得的进展；证据汇总改变 Advisor 的指派，指派与局部反馈改变 Worker 的下一实验 | 印象与近期消息进入上下文，影响相处方式；主模型也可查询印象、翻旧消息 |
| 如何改进 | 追加成败经验，取用时重估各动作回报与基线，修正当前选择；所查代码不回写旧经验评分 | 独立评测后更新局部候选与全局最好；Advisor 根据跨方向结果调整实验；保留局部进展允许新路线成熟 | 模型追加印象、删除或替换旧印象；数量／冷却触发整理，缺印象成员可从近期发言补提炼；未验证独立结果驱动的优化闭环 |
| 如何选择／检索 | 按当前处境寻找可比较经验，再按动作聚合估值；两个实验域采用不同筛选实现 | harness 按角色、方向与任务重建上下文；主方案中 Worker 没有自由读取全档案的入口，也不是通用向量检索 | 窗口与成员 ID 精确匹配；自动注入该成员印象数组末 3 条，显式查询可取其余印象；历史工具按窗口翻近期／更早记录 |
| 遗忘可以确认到哪里 | 所查长期库未见自动按年龄衰减或淘汰；本轮不命中不删除，旧样本仍可能再次参与统计 | 可确认的是会话不继承、活动候选更新与上下文选择；不能据此声称 harness 已删除旧实验知识，论文未给出一般性证据淘汰规则 | 印象整理实际合并、覆盖、截短与删除；部分替换保留最近一份备份。聊天原文默认不限量，另有配置截断与管理删除入口 |

**JitRL 的源码与论文要分开。** 本次只读核对官方仓库 `143d22185d95fbf633a0befe6861d5e8b732543b`：Jericho 的 [长期库](https://github.com/liushiliushi/JitRL/blob/143d22185d95fbf633a0befe6861d5e8b732543b/Jericho/src/cross_episode_memory.py#L259)持续追加步记录和后续奖励序列，[检索](https://github.com/liushiliushi/JitRL/blob/143d22185d95fbf633a0befe6861d5e8b732543b/Jericho/src/cross_episode_memory.py#L390)以历史／当前状态两个 FAISS 索引召回，再以词集合相似度重排与筛选；WebArena 的[主检索路径](https://github.com/liushiliushi/JitRL/blob/143d22185d95fbf633a0befe6861d5e8b732543b/WebArena/memory_agents/utils/cross_episode_memory.py#L485)则扫描 metadata，先要求归一化 URL 相同，再筛任务、历史相似度。论文用 top-k 描述邻域，当前代码未在所有返回路径上严格截断到 k。状态召回之后再[按动作统计回报并修正分数](https://github.com/liushiliushi/JitRL/blob/143d22185d95fbf633a0befe6861d5e8b732543b/Jericho/src/jitrl_agent.py#L260)，不能把两域概括为统一向量库算法。失败样本也入库，但不同域的环境奖励与模型评分进入回报的规则不同。

`γ` 折扣的是一个动作之后各步奖励的距离，不是旧记忆随存储年龄衰减。当前 episode 步数改变检索阈值也不等于记忆变老。另一个 `self.memory` 列表存在 FIFO，但 JitRL 主动作路径中的追加调用被注释，不能把它当长期库的遗忘机制。两个域都有显式清空入口，但未发现主流程自动调用；本次只核对源码，没有复现训练或运行效果。

**SLA 的关键是记忆归属与取用规则。** [正文 §3](https://arxiv.org/html/2610.07625v1#S3)与[附录 B.1](https://arxiv.org/html/2610.07625v1#A2.SS1)把研究状态留给 harness；候选代码本身已保存部分实验进展，失败记录又影响以后选择什么。更换活动候选是更新直接使用的做法，失败记录未进入某个 Worker 本轮上下文是选择，两者都不能自动解释为整个系统已经遗忘。清空会话还会阻止未被 harness 接收的推理持续影响下一轮，但它不是对持久证据库的淘汰。正文与 PDF 附录 E 未披露完整摘要算法和一般性的知识过时／删除协议，作者仓库也未发布可复现实现。

**QQ-agent 的印象与原始经历分属不同载体。** [formatForPrompt](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/memory.js#L455)的 `slice(-3)` 仅控制自动可见条目，遗漏印象仍可显式查询；[整理](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/orchestrator.js#L1727)则以模型对现有印象的判断实际替换文件，二者不能混称遗忘。替换并未形成完整历史版本链，只保留最近一次成员备份；删除工具本身不新建备份。原消息[默认无条数上限](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/config.js#L253)，但 [store](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/store.js#L304)提供清空、按记录删除与非零上限截断。因此可以说它删减了当前人物表征，不能说对应经历一定从整个系统消失，也不能保证旧原文会被自主重新发现。未公开的分层扩展仍在本比较范围之外。

**遗忘的比较必须回到恢复与影响。** 本轮不取用、持久表征被改写／删除、恢复渠道被关闭，是三种不同事实。原文仍在而入口消失，可能形成无法想起的功能损失；旧条目退出本轮切片而有关线索再次出现时能恢复作用，则只是选择。上述公开证据足以说明三者的载体与取用规则，尚不足以给它们的功能性遗忘或终身记忆效果排名。

## 视频身份与覆盖范围

[BV183b56AE3d](https://www.bilibili.com/video/BV183b56AE3d)的公开元信息：标题「可能是上手最简单门槛最低的肥鱼QQ机器人？」；UP 为 Kondius；2026-09-08 17:17:59（UTC+8）发布；单 P，6 分 16 秒。简介明确使用 QQ-agent 名称。调查定位到[K0nd1us/QQ-agent](https://github.com/K0nd1us/QQ-agent)，它与 tencent-connect/dsh-qqbot 是不同仓库。

本次使用 bili-note 的标准库提取器核对公开元数据与字幕接口。普通与签名 WBI 接口都返回 `need_login_subtitle=true`、空字幕列表；这表示本次匿名访问未取得字幕，不能断言视频没有字幕。另取得部分公开评论，其中有明确仓库链接，作者也说明每次唤醒新会话、聊天 JSON 持续增长、再从中总结记忆。用户选择只调查字幕和源码，未下载关键帧、音视频，也未读取登录凭据。以下机制来自公开仓库源码，不能冒充视频口播；现版源码也可能晚于视频演示版本。评论仅作项目身份与作者说法的旁证，不复制评论账号和正文到仓库。

## QQ-agent：成员印象与无状态会话

核对快照为 `11577baad321d1774caf0765d791c211072bfa83`。该项目由 qq-bridge 改造而来；其 README 所称「双向长期记忆」不能自动解释成双向图检索，下面按实际读写路径说明。

| 环节 | 实际机制 | 固定源码出处 |
|---|---|---|
| 原始经历 | 每个会话一份持续增长的消息 JSON，含已读标记；入站即写，临时文件再 rename | [store.js](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/store.js#L21) |
| 长期载体 | 按会话目录、成员文件保存姓名、印象文本、创建／更新／整理时间；同一个人在不同会话不自动共享一份档案 | [memory.js](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/memory.js#L1) |
| 日常写入 | `memory_append` 调用 append，限稳定印象；完全相同文本去重，每条截至 300 字符 | [tools.js](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/tools.js#L649)、[append](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/memory.js#L203) |
| 显式查询 | `memory_query` 取当前会话印象，可按成员过滤；这条路径没有 embedding 或向量检索 | [query 工具](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/tools.js#L678) |
| 自动消费 | 从触发消息、历史窗口或锚点取得成员集合，精确筛选后注入 `【记忆】`；每人当前印象数组末 3 条，不按本轮语义相关性排序。无成员 ID 的旧数据始终加入；不传过滤集合时默认取更新最近的 15 名成员 | [prompt.js](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/prompt.js#L806)、[formatForPrompt](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/memory.js#L455) |
| 修订 | 模型可 `memory_remove`，管理界面可编辑或替换成员印象；replaceMember 会备份原文件 | [remove 工具](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/tools.js#L700)、[replaceMember](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/memory.js#L321) |

自动整理在聊天流程后检查：默认总印象数超过 4，或者任一成员超过 5 条，再满足默认 6 小时冷却，才安排整理（可配置或关闭）。模型合并／删除已有印象；返回条数增加就放弃结果，默认最多留 5 条、每条截至 120 字符。依据见[触发条件](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/orchestrator.js#L1444)与[整理执行](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/orchestrator.js#L1727)。这只是数量限制和 prompt 约束，不是证明没有编造或丢失事实。

更值得注意的是[源码中的实际失败说明](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/orchestrator.js#L1724)：有人聊了 200 多条仍然零印象，日常模型几乎不主动调 memory_append，于是整理入口补了「发现新人」。批量整理可纳入发言达到默认 20 条、但没有印象的人，每次最多 3 人；从最近 2000 条消息中取该成员最后 40 条、每条最多 200 字符，再提取稳定特征。这解决一个观测到的写入缺口；并不表示完全空记忆会仅因发言增多自动触发整理——自动入口仍受上面的印象数量条件限制，手动整理可以另行触发。

**迁移推论**：这是一个很小的完整闭环：JSON 存档 → 印象写入／补提炼 → 成员筛选 → 新会话再次消费。可以比较按成员选择上下文是否减少无关阅读；尤其应测试只提供写入工具，与明确整理触发相比，会不会长期漏写。

**边界**：成员印象主要是短文本投影，条目没有逐条消息来源引用；完全相同文本去重不解决语义矛盾。直接重写、截短和删除也不同于柚子的「覆盖隐藏、原文与正式号保留」。按人选记忆还会漏掉事件、承诺和关系跨人发生的内容。现版 prompt 另有锚点与新增已读信息机制，README 的「每次成本恒定」不能当作严格 token 上界或已测效果。

## dsh-qqbot：恢复与压缩由宿主完成

插件快照为 `c5f2d8c50dd2bbaec3f53ec50d00881ac5c711aa`；宿主 DeepSeek Harness 快照为 `d743267388641bc76f17c45ce8b4c231aed1d32c`。两者分别核对当前源码，没有启动或验证组合运行；插件声明的是版本范围，不是这个宿主 commit 的固定依赖。

1. **稳定定位会话**：插件按应用、聊天类型和对端组成 key，SHA-256 派生 sessionId；存在用户记录的新 ID 时优先使用。取得会话依次尝试插件内存 Map、宿主存活 agent、`agents.resume`、新建。[SessionManager](https://github.com/tencent-connect/dsh-qqbot/blob/c5f2d8c50dd2bbaec3f53ec50d00881ac5c711aa/src/session/session-manager.ts#L430)
2. **插件自己的持久文件**：保存模型覆盖、sessionId 与 preset 偏好，没有聊天正文或事实记忆。群旁听历史用 MemoryHistoryStore，是临时缓冲；被提及时拼入消息，提交 followup 后就清空。[prefs-store](https://github.com/tencent-connect/dsh-qqbot/blob/c5f2d8c50dd2bbaec3f53ec50d00881ac5c711aa/src/model/prefs-store.ts#L15)、[inbound](https://github.com/tencent-connect/dsh-qqbot/blob/c5f2d8c50dd2bbaec3f53ec50d00881ac5c711aa/src/transport/inbound.ts#L129)
3. **宿主事件持久化与恢复**：示例组合加载 session-persistence-jsonl。宿主监听 session/event 与 flush，追加会话事件；resume 用 open/read 读事件重建 session，再 deriveMessages 形成模型可见消息。这是按 sessionId 恢复会话，不是语义检索。[插件组合](https://github.com/tencent-connect/dsh-qqbot/blob/c5f2d8c50dd2bbaec3f53ec50d00881ac5c711aa/cordis.yml#L89)、[事件监听](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/session/session-persistence-jsonl/src/storage.ts#L535)、[恢复](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/core/agent-loop/src/index.ts#L843)
4. **压缩与更新**：插件 `/compact` 调宿主 compaction 服务。宿主默认压力阈值比例 0.8、保留比例 0.16，自动模式开启；摘要模型生成 checkpoint，然后追加 compaction/summary 与携带 replace surface 操作的 user/message。旧事件仍在日志，模型可见区间改为摘要。[插件 compact](https://github.com/tencent-connect/dsh-qqbot/blob/c5f2d8c50dd2bbaec3f53ec50d00881ac5c711aa/src/session/session-manager.ts#L573)、[宿主默认配置](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/compaction/compaction-basic/src/config.ts#L20)、[摘要提交](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/compaction/compaction-basic/src/region.ts#L491)
5. **独立跨会话记忆**：宿主文档另提供默认关闭的 Memorix、MCP Reference Memory、Engram 接入示例。DSH 只暴露工具，具体存储与检索由服务负责，模型还需实际调用；不应把可选接入说成 QQ 插件已经自动提取和回忆。[第三方记忆说明](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/docs/user/guide/mcp-memory.zh.md#L5)

**迁移推论**：事件事实与模型可见投影分开，压缩追加新记录而非抹去历史，与柚子的既有方向相容。恢复同一会话、压缩可见历史、跨新会话检索事实仍应分别验证。

**边界**：插件接入依赖宿主已有的持久化与压缩组合；群临时缓冲不会因 session resume 自动恢复。当前插件有读取 preset 时假设 persistence.load 的代码，而所查宿主恢复链采用 open/read，两仓 HEAD 不构成兼容运行证明。没有发现插件自建自动成员画像或向量检索机制，也没有核验外部 memory MCP 的效果。

## 对柚子最有价值的可比较实验

### 补读 prompt.js：群聊行为本身也是研究对象

上一轮集中核对核心记忆读写，只检查了 prompt 的成员注入段，没有完整评价提示词。维护者指出这个遗漏后，本次完整读取同一 commit 的 890 行 prompt.js，并核对 orchestrator 的组装与结束路径。下列是源码设计判断，不是经过对照实验的效果结论。

- **把参与决策写具体**：subjectivity、speakOrNot、notAQueue、humanRhythm 与 notModerator 连起来，明确哪些情况下有话值得说、哪些情况下别人已经答好或话题已结束；允许等待、漏看、停止，不要求逐条回复。既限制抢话，也允许无人点名时因自身兴趣加入。[行为段](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/prompt.js#L66)
- **把界面动作翻译成参与者动作**：发消息、翻聊天记录、发表情分别对应工具；普通文本不自动发送。这样结束运行不必成为一条给群友看的报告。orchestrator 默认无工具调用时结束；另有可选回复抢救插件，不能把提示词说明当成所有配置下的绝对发送边界。[工具协议](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/prompt.js#L35)、[结束与抢救](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/orchestrator.js#L1086)
- **记忆影响判断而非强制展示**：memoryRules 要求只在自然相关时提起旧印象；与开口条件结合，记忆可以帮助选择话题、避免雷点，而不用把“我记得你”挂在每次回复上。[记忆行为规则](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/prompt.js#L133)
- **删除会诱导坏行为的上下文**：源码解释删除群名、消息密度与距上次发言时长的状态段，因为模型会据此生成播报式开场。时间戳仍在消息中，完整当前时间放末尾。这是作者报告的实用修订原因，尚非独立复验。[删除说明](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/prompt.js#L843)
- **把成本考虑落实到文本组装**：核心规则在前，动态 Skill 段在后；用户侧稳定内容先于新消息与时钟，锚点与追加额度控制滑窗变化。当前代码仍每轮重算记忆文本，锚点保存的是 ID／成员集，不是完整字节快照，所以记忆变化等仍可打断前缀；“锚点一定字节不变”是意图，不能直接作为已证事实。[系统组装](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/prompt.js#L217)、[锚点算法](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/prompt.js#L632)

也有不能直接移植的选择：antiAiFlavor 将故意乱回和给无用答案当作拟真手段，它是特定人格的偏好，不是主体性的必要条件；securityRules 假定没有本地工具，与柚子的直接能力不符。被点名必须回复与可以沉默的规则需要按场景解释，不能由一句“像真人”消除张力。固定短消息长度也不适合所有任务。

文件开头明确说行为规则移植自 qq-bridge 的 qq-chat-v2 preset，当前项目继续改了触发与上下文构造。应区分原规则来源、后续修订和实际表现；完整补读不意味着已经验证生成效果。collectSkillSections 与 before-context／before-llm-messages 还有扩展注入入口，核心成员印象并非全部可能的记忆机制。

### 上一轮漏报的扩展记忆线索

本次补查确认：当前公开仓库没有 conversation-memory、knowledge-memes、memory-recall 的实现或 manifest。[README](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/README.md#L88)说明扩展落点出厂为空，[loader](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/src/plugin-loader.js#L458)只加载实际存在的目录；不能由 prompt 中出现名字就认定默认启用。

但这也不能解释为没有额外记忆设计。[2026-09-17 的 develop 提示词管线记录](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/doc/extend_development/prompt-pipeline.md#L94)描述 conversation-memory 将跨轮记忆追加到 system 尾部，knowledge-memes 在知识库联想命中时另加脑内联想消息，也列出 memory-recall 片段。[发布脱敏脚本](https://github.com/K0nd1us/QQ-agent/blob/11577baad321d1774caf0765d791c211072bfa83/scripts/sanitize-release.mjs#L281)则记录小时摘要与原文片段、语义卡、跨轮草稿及已发内容，并清理 memory-v2、semantic-cards、cross-turn、cross-chat 等目录。

这些相互支持作者扩展环境存在另一套分层／跨轮记忆；上一轮只报告成员印象，覆盖面不足。当前仍未取得扩展源码，具体持久格式、召回算法、更新策略与安装默认值未知；尤其不能凭「语义卡」推断用了向量检索。应把当前公开核心的已验证机制，与历史扩展文档中的更丰富设计分开记录。

### 候选实验

以下是迁移推论，尚未实施：

- 用同一冻结输入比较「仅给整理／写入工具」和「明确安排一次整理」，记录实际写入、漏写、成本以及后续是否消费；QQ-agent 的失败说明支持先查这个缺口。
- 保持同一份来源记忆，只改上下文选择：全量投影、按成员、按主题／任务入口，比较漏掉的跨人事件与关系；不因成员筛选便宜就先固定为唯一索引。
- 分开验证重启恢复、全新上下文召回、事实纠正、低频事实使用。摘要更短或磁盘文件存在不能代替这些观察。
- 借 SLA 在同一 checkpoint 上比较显式指派与自由选任务，区分「已测无收益」和「运行失败」，同时记录累计 token 与真实费用；先用可重复的检索／整理任务建立证据。

柚子已有正式经历、覆盖与反查底座。优先研究的是它何时整理、怎样找到、后续是否使用；上述工程项目没有给出足以要求另建一份成员数据库或替换中心主体的证据。

## Bili Note 的持久复用

[bili-note](https://github.com/Rimagination/bili-note)是材料提取与笔记 skill：原文、字幕、评论、元信息和证据索引落盘，供人和 agent 回查；它本身不提供 Bot 的自动长期回忆策略。

已用 skill-installer 将 `08fe81297ef876a668a7e187409fa2c3366b9faa` 固定安装到本机 Codex 的 `/root/.codex/skills/bili-note`，后续轮次可发现并使用。没有安装进柚子的 `mods/tools`，也没有启动 Bot。Python 3.12 与基础脚本检查通过，公开接口可达；Chrome web-access 和 Edge 调试接口均不可用，Qwen3-ASR 环境缺失。已有 ffmpeg、yt-dlp 和 faster-whisper，但本次没有使用音频转写。

本机再次匿名探测的命令如下；临时输出需要另行归档才会长期保存：

```bash
python3 /root/.codex/skills/bili-note/scripts/extract_bilibili.py \
  BV183b56AE3d --out /tmp/qqbot-video-probe --parts all --download-subtitles
```

若已有可取得字幕，归档脚本可保存原始材料与时间／证据索引。长期保存应保留原文和索引一起，而不是只有生成的笔记。本次未取得字幕，留下的是身份、失败边界和源码调查，不是视频全文笔记。
