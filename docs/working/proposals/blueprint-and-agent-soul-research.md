# Blueprint 与 Agent Soul：上下文组织和记忆机制调查

> 2026-10-31，只读公开资料与源码。用于接续[上下文、预测与通透](context-prediction-and-permeability.md)的讨论，不决定引入任何项目，也不把源码检查写成运行效果验证。未安装、启动应用或调用付费模型。

## 阅读范围

| 项目 | 核对版本 | 主要资料 |
| --- | --- | --- |
| [Blueprint](https://github.com/wbohui123-sys/blueprint) | `8f0bb0e84e04fd3a74f371805ebd1245916c81a6` | 中文 README、完整 Developer Handbook、提示文字文件表及相关调度、上下文、成果发布源码 |
| [dsh-agent-soul](https://github.com/cyanwell/dsh-agent-soul) | `92cf1301aedeefdda9fefc37ff0f595c3dd4cb36`，包版本 `2.0.0` | ECHO 权威口径、与 mnemon 的协同说明、ECHO/VITAL 说明、架构缺口清单及相关插件、索引、检索源码 |

用户提供的 Blueprint [使用文档](https://f0peyphsfh7.feishu.cn/docx/QdaldFmE2oDcCexO69mcdhQnnwg)和[合伙人手册](https://f0peyphsfh7.feishu.cn/docx/ZJPzdXBvMoekMdx9Uvac11ypnkf)能在公开浏览器打开；本次只读到首段与目录，未取得完整正文。既有飞书 CLI 因缺少可用的 user 授权未能读取。技术分析依据仓库中的完整[英文手册](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/docs/developer-handbook.md)，未宣称两份手册全文完全一致。

两个项目都依托 DeepSeek Harness。Blueprint 提供可视化任务组织；Agent Soul 提供记忆、身份、检索与运行维护的插件和工具组合。

## Blueprint：任务展开与条件汇聚

当前发布入口面向 Apple 芯片 Mac，使用文档要求 macOS 13 及以上，用户提供模型 API Key。仓库采用 AGPL-3.0。项目自己的成本示例包含中途截断的运行，不能当成完成同类任务的稳定报价。见[中文 README](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/README.zh-CN.md)。

### 目标如何进入不同上下文

`helm` 管总目的和阶段，`dock` 管分工与整合，`run` 完成具体的一块。分工由模型判断；子节点创建独立会话，继承工具组合，按请求接收任务、联系、情境、工作等级、同层分工和规则，不复制父节点整段历史。每个节点使用自己的 Git 分支与工作区，成果向上合并。

helm/dock 在阶段报告、孩子交齐、请求改写等边界按节点文档重建上下文：保留请求、职责、结构、记录原文，旧会话仍在宿主账本中。run 则使用宿主的默认摘要压缩。这是两种实际不同的上下文处理方式。见[新开节点](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/node/src/会话.js#L106)与[重建原文](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/node/src/上下文重建.js#L88)。

### 结果并不只沿父子树返回

派出一批工作后，上级登记等待并挂起 Goal，等这一批交回再汇聚。任务未达成也能以 `unmet` 交回；运行中的后代未交回时禁止父节点结束，尚未开工的待做任务则会随父节点结束撤销。因此，返回齐全与目标全部成功是不同的事实。见[等这一批交回](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/node/src/策略.js#L271)及[结束节点](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/roster/src/index.js#L357)。

源码还实现了跨树的成果发布与等待：

- 节点可以发布 `key / version / summary / entries`，向项目内多个节点发送通知，不要求对方是父节点或孩子。
- 消费者可以等待指定节点的某项成果达到某个版本，也可以等待节点交回、孩子、用户输入或后台任务；条件支持 `all` 和 `any`。
- 发布或名册变化触发条件复查；满足条件后锁存事实并唤醒等待者。生产者已结束却没交出所需成果，也会返回不可用的事实，让消费者继续判断。

见[发布和等待实现](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/node/src/条件等待.js#L119)与[条件读取](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/roster/src/信息依赖.js#L22)。因此，不能把它缩写成只能向上交差的任务树。

新版成果会通知仍在工作的接收者，但已满足的等待不会因此自动重算；是否采用变化由模型继续判断。当前查到的预测/策略指纹也没有纳入已消费成果的版本，未构成沿语义依赖自动撤销所有下游判断的机制。

### 复用已有结果和经验

交齐汇总给新结果全文，已经送过的结果折成一行入口；需要时再读节点文档。项目经验按条保存，醒来列标题与路径，由节点按情境选读。见[交齐汇总](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/roster/src/组织.js#L76)和[交回记忆](https://github.com/wbohui123-sys/blueprint/blob/8f0bb0e84e04fd3a74f371805ebd1245916c81a6/blueprint/packages/node/src/交回记忆.js#L39)。

作者在技术手册中明确写出：经验仍限于单个项目，积累后的整理、合并、退役和跨项目使用尚待完善；主要成本仍包括重复阅读与验证。“充分上下文下 AI 直觉几乎不会出错”是作者的出发假设，手册没有把它写成已证明结论。

## Agent Soul：可回原文的记忆与维护工具

主包注册架构说明、规划、脚手架、检查、安装建议等工具；实际注入上下文的是附带的 memory 插件，把 SELF、当前状态和原文入口条款送入系统提示。热记忆与语义图依赖外部 mnemon，不能把主包、附带插件和外部依赖混成一个单独功能。见[主包入口](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/index.js#L53)与[上下文注入](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/plugins/memory/lib/index.js#L307)。

ECHO 把身份和热记忆、当前工作上下文、原始日志与账本、索引和图记忆、检索入口区分开。它强调压缩产物留下 `log:<session>#seq=N` 等原文指针，并区分一手材料、索引和程序信息。`memq` 能按词查找、查看时间线并按指针回原文。见[ECHO 权威口径](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/docs/ECHO-权威口径.md)与[检索源码](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/kit/echo/memq.mjs#L366)。

索引有增量机制：日志未变就复用旧分片，追加日志可从上次帧边界续读；它提取已有 `compaction/summary` 事件，不是在重建索引时另做模型反思。ECHO 面板定时体检，显式重建操作则依次执行索引重建和 SELF 装配。本次未找到仓库交付的每日 04:00 自动重建任务，不能把作者运行环境中的安排当成安装包必带的能力。见[增量索引](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/kit/echo/memindex.mjs#L284)与[体检和重建](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/plugins/echo/index.js#L435)。

10 月 30 日的权威文档补充直说当前更接近档案，记忆代谢尚缺。源码中的具体对应是：账本没有关闭事件，`memq now` 把全部 `promise/shelved` 都算未结；旧结论失效仍需阅读者判断。这是现有状态推导与更新机制的缺口，不能仅凭只追加存储方式就断言任何系统都无法更新认识。见[账本类型](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/kit/echo/mo.mjs#L41)与[未结推导](https://github.com/cyanwell/dsh-agent-soul/blob/92cf1301aedeefdda9fefc37ff0f595c3dd4cb36/kit/echo/memq.mjs#L291)。

仓库采用 MIT。用户贴出的 PowerShell 入口面向 Windows 桌面安装；部分插件依赖源码/CLI 宿主内部模块，不能视为所有桌面版本都能完整装配。仓库包版本确认是 `2.0.0`；本次未独立找到“V2 维护、V3 待验证”的公开发布声明，保留为用户转来的作者说法。

## 与当前讨论的关系

以下是 Codex 的阅读判断，不是两个作者或草籽已经确认的同构关系。

Blueprint 提供了可以具体追踪的“展开后汇聚”：分散任务并行工作，在明示的成果/返回条件处等待，随后让一个节点继续判断。它的任务组织树与跨节点成果关系也可以分开看。这里可以观察一组有限条件怎样形成当前判断的汇合点；其范围是已登记的任务和成果条件。

Agent Soul 提供的是另一处支撑：已经压缩或离开当前窗口的材料，怎样保留可寻回的入口，以及怎样观察这些入口是否还在工作。它的文件索引增量复用与我们讨论的“语义输出不受某次环境变化影响，因此继续使用”分属不同层面的判定。

两者分别使计算组织和材料追溯变得具体。接续讨论时，可以直接看这些已有机制怎样工作；不必把“没有穷尽世界”重新提出为反对理由，也不把可回原文、条件齐备或任务完成本身当成长期预测能力已经形成的证明。
