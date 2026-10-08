# 记忆基础问题综述：按开放问题组织

> 状态：外部研究综述（初稿 2026-10-06；预测编码专题与推论校准 2026-10-08）。它按[基础问题讨论](memory-foundations-discussion.md)留下的开放问题组织已有工作，不是运行合同，不批准实现，也不替维护者决定记忆系统的目标。

## 为什么写、怎样读

[讨论文档](memory-foundations-discussion.md)形成了一组工作假设：预测统一写入、读取与学习；行动是对观测的补全；误差落在编码器（比喻地说，测量函数）的输出上；二阶惊奇处理噪声；无常的可达性是统一判据。讨论后期，这些思路频繁与现成领域重合，且一度依赖约 15 条凭记忆的引用。本文的目的有两个：核对这些引用，并找出已有工作对每个开放问题给出了什么。

旧的[记忆与自我演化研究地图](memory-and-self-evolution-research.md)覆盖的是 agent 记忆文献（MemGPT、RAPTOR、Reflexion 等），对应旧问题框架。本文覆盖计算神经科学与表示学习，两者互补，不替代。

**按问题组织，而不是按领域组织。** 草籽担心少数例子或某个领域的框架会把目标收窄，所以每个问题只回答四件事：

1. 已有工作给出了什么答案，证据有多强；
2. 它依赖哪些前提，这些前提在柚子身上是否成立；
3. 与讨论文档的判断哪里一致、哪里冲突；
4. 能推翻我们假设的最小实验是什么（只是候选，是否运行由维护者决定）。

**证据强度标记**：**强**＝多项独立实验或有明确前提的数学证明；**中**＝特定任务上的实验结果；**弱**＝理论主张、类比或尚有争议。标记只修饰紧邻的主张：定理对某种网络成立，不提高“脑采用它”或“柚子应采用它”的证据等级。

**核对方式与边界。** 2026-10-06 初稿只核对了搜索结果、元数据与摘要，当时未取得 arXiv 与 YouTube 原文。2026-10-08 补读预测编码论文的相关正文、方法、定理和更正，并复核 VICReg、集成分歧、层级高斯滤波器与主动推断的关键段落，以及 PSR 的有限状态定理和反事实影响方法的对象；不声称逐页精读所有论文。文末[预测编码题单](#预测编码题单与版本核对)逐项记录读取层级，未取得的全文仍明确保留。向柚子的迁移一律是推论。视频仅取得官方标题与描述，未观看、未取得可用字幕。

## 问题一：编码器的训练目标与防坍缩

**对应讨论文档**：十二节第 2、3 条（编码器形态与训练目标；测量空间中的合成还是纯选择）。

### 已有答案

- **在表示空间预测，而不是在原始数据上生成。** LeCun 的立场文件《A Path Towards Autonomous Machine Intelligence》（2022，OpenReview）提出 JEPA：预测目标的表示而非目标本身，目标编码器可以丢掉不相关的细节（纹理、噪声），使表示更抽象、更可预测；它要求表示“同时最大化信息量与可预测性”。I-JEPA（Assran 等，CVPR 2023）是图像上的实例：从一个上下文块预测其它目标块的表示，不做像素重建。证据：**中**（视觉下游任务表现强，但目标是表示质量，不是持续学习）。
- **对比式预测：选择，而不是生成。** CPC（van den Oord、Li、Vinyals，2018）用自回归模型在潜空间预测未来，以概率对比损失（InfoNCE，一个 N 选 1 分类）训练，使潜空间保留对预测未来最有用的信息；在语音、图像、文本和 3D 强化学习上有效。证据：**中**。这就是讨论文档所说的“纯选择”。
- **防坍缩。** VICReg（Bardes、Ponce、LeCun，ICLR 2022）用两个正则项显式防止编码器输出常数或无信息向量：方差项让每个维度的方差保持在阈值之上；协方差项让每对维度去相关。证据：**中**。

### 前提是否成立

这些方法都靠梯度端到端训练编码器，并依赖大量同分布数据（如数据增强产生的同一图像的不同视图）。柚子的事件流量小、非平稳，而且编码器要在线更新（见问题四）。“预测表示而非原文”的核心理由（原文细节大多不可预测）在聊天文本上同样成立；数据量与在线性两条前提不成立或尚未知。

### 与讨论文档的对照

- “不生成原始未来、在表示空间比较”与 JEPA、CPC 的动机相近，但编码实际输入仍有成本；预测表示也不是从候选中选择的唯一实现。逐 token 评分还可对已知目标做条件评估，不必先采样一份完整文本，是否取得这种评分能力则要另查。
- **更正与收窄**：[VICReg 原文 §2、§4](https://arxiv.org/pdf/2105.04906)的不变性项是同一样本两个视图的嵌入距离，不是未来预测误差。方差与协方差约束发生在样本批次上；“应／静”的对应只能作为类比。编码器完全可以靠无关噪声维持方差，行动也完全可以忽略一个变化丰富的表示；防坍缩不证明变化有效抵达了行动。
- 未核对：I-JEPA 具体如何防坍缩（通常认为依赖非对称结构与目标编码器的滑动平均），摘要级材料未说明。

### 候选证伪实验

先冻结句向量模型，只训练“前文 → 后续表示”的小预测器，检验这个目标是否有留出预测增益；这一步不能检验编码器坍缩，因为编码器根本没更新。只有进入联合训练后，才比较有无防坍缩约束。两者都用同一冻结参照编码器或独立下游观测核验，避免候选自己改变目标坐标后显得误差更低。若表示方差保持了、但留出预测与后来使用均不改善，防坍缩只证明没有常数解。

## 问题二：二阶惊奇、精度与噪声

**对应讨论文档**：第九节；十二节第 7、9 条。

### 已有答案

- **只看预测误差的好奇会被噪声困住。** Burda 等（2018）对纯好奇驱动学习做了 54 个环境的大规模研究，报告了基于预测误差的奖励在随机环境中的局限，即“嘈杂电视问题”。证据：**强**（被多项后续工作复现和引用）。
- **学习进步而不是误差。** Schmidhuber（2008《Driven by Compression Progress》；2010《Formal Theory of Creativity, Fun, and Intrinsic Motivation》）：数据在观察者学会更好地预测或压缩它时才“暂时有趣”；预测器的学习进步就是强化学习者的内在奖励。Oudeyer、Kaplan、Hafner（2007，IEEE TEC）的 IAC 让机器人趋向学习进步最大的情境，因而聚焦于“既不太可预测、也不太不可预测”的情境，并自组织出阶段式发展。证据：**中**（机器人与模拟实验）。
- **婴儿的金发姑娘效应。** Kidd、Piantadosi、Aslin（2012，PLoS ONE）用理想学习者模型度量刺激复杂度（负对数概率），发现 7–8 个月婴儿在复杂度很低或很高时最可能移开视线。证据：**中**（单项研究，两组实验）。
- **对噪声稳健的探索。** Pathak、Gandhi、Gupta（ICML 2019）训练一组动力学模型，以它们的**分歧**作为探索奖励，在随机环境中仍然有效。RND（Burda 等，2018）预测一个固定随机网络的输出，目标本身是确定的。证据：**中**。
- **精度与注意。** Feldman & Friston（2010）：感知是对感觉原因的推断，注意是对这些原因的**不确定性（精度）**的推断；精度由报告预测误差的单元的突触增益编码。证据：**弱到中**（理论与神经模拟）。
- **层级的不确定性。** Mathys 等（2011）的层级高斯滤波器：除第一层外每层是高斯随机游走，步长（方差）由上一层决定，从而在多层上表示环境波动性与感知不确定性。证据：**中**（被广泛用于拟合人类学习行为）。
- **两种不确定性。** Kendall & Gal（NeurIPS 2017）：偶然不确定性（aleatoric）是观测固有的噪声；认知不确定性（epistemic）是模型的不确定性，给足数据可以消除。

### 前提是否成立

上述方法大多在可重复、可大量采样的环境中验证。柚子的聊天流不可重置，同一情境很少重复，学习尝试的样本少；二阶统计（误差的方差）需要比一阶更多的数据才能估计稳定。这是最主要的前提缺口。

### 与讨论文档的对照

- **相近，但不是两个现成的同一机制。** [Pathak 等 §2.1–2.2](https://proceedings.mlr.press/v97/pathak19a/pathak19a.pdf)衡量不同模型的预测分歧，训练充分时随机目标的均值可以被共同学会；它不预测“这次学习会成功多少”。[Mathys 等的生成模型与更新规则](https://www.frontiersin.org/journals/human-neuroscience/articles/10.3389/fnhum.2011.00039/full)让上一层调节下一层状态转移的条件方差，区分状态、波动性和推断不确定性，也不等于任意地再预测一次误差。
- **精度不是误差大小。** [Bogacz §2.2、§2.4、§5.1](https://www.tnu.ethz.ch/fileadmin/user_upload/teaching/cpcourse/2020/Literature/Bogacz_2017.pdf)中精度是方差／协方差的逆，残差由它加权；若方差也可学习，高斯目标还含 `log(方差)`，不能靠无限增大方差免费消除误差。误差稳定、条件噪声大、模型间分歧小、学习结果可预测，是四个不同判断。
- 时间中的更多数据有助于区分噪声与尚未理解的结构，但不保证可以区分：模型共同偏误会使分歧很小；稳定地学不会也可能是模型容量不足。随机噪声的分布可以稳定，单次误差仍随机，不能无条件写成“二阶惊奇为零”。
- 讨论文档的推进之处（推论）：已有工作把这一机制用于感觉通道或探索奖励；草籽主张把它用于包括学习过程在内的一切，并由同一规则产生和解除屏蔽。这一统一没有找到直接对应的已有工作，既可能是新意，也可能是尚未暴露的问题。

### 候选证伪实验

在冻结档案中插入一个合成的“噪声通道”（例如随机生成的刷屏消息）。分别以一阶预测误差和集成分歧作为注意信号，观察前者是否被噪声通道吸住、后者是否忽略它，以及当噪声通道中途变为有规律的内容时，后者能否重新关注。若集成分歧在柚子的数据量下也被噪声吸住，“二阶惊奇”需要更多样本或别的估计方式。

## 问题三：快慢两套系统与回放

**对应讨论文档**：第二节“记忆是过去影响现在的方式”、第四节“读即写”、第七节“记忆是一种器官”；本问题是讨论中完全没有覆盖的领域。

### 已有答案

- **互补学习系统（CLS）。** McClelland、McNaughton、O'Reilly（1995，Psychological Review）：记忆先以突触变化存入海马系统，这些变化支持近期记忆在新皮层中的重新激活，新皮层每次重新激活只改变一点，远期记忆建立在新皮层累积的变化上。海马是稀疏、模式分离、快速学习情景的系统；新皮层是分布式、重叠、逐渐整合多个情景以提取潜在结构的系统。证据：**强**（解释海马损伤后近期与远期记忆的分离，并有大量后续支持）。
- **CLS 更新。** Kumaran、Hassabis、McClelland（2016，Trends in Cognitive Sciences）：回放允许按目标加权经验统计；海马痕迹的反复激活可以支持某些泛化；**与已有结构一致的信息，新皮层可以快速学会**。证据：**中到强**。
- **睡眠回放。** Wilson & McNaughton（1994，Science）：行为中一起放电的海马位置细胞，在随后的慢波睡眠中一起放电的倾向增加。证据：**强**（开创性实验，后续大量复现）。
- **再巩固。** Nader、Schafe、LeDoux（2000，Nature）：已巩固的恐惧记忆在提取时回到不稳定状态，此时阻断蛋白质合成会导致遗忘。证据：**强**（但其普遍性与边界条件仍有讨论）。

### 前提是否成立

CLS 的核心前提是：存在一个快速、逐条保存的情景存储，和一个缓慢、靠重复整合的结构学习者。柚子身上两者都有现成对应：chatlog 与 oplog 是快速、逐条、只追加的情景存储；可训练的小编码器（若采用）是慢学习者；主 LLM 是第三种东西，即一个冻结的、预训练得到的巨大先验。

### 与讨论文档的对照

- **这是值得补入的比较框架。** “LLM 黑盒 + 可训练编码器 + 原始档案”在功能分工上类似 CLS，但文件档案不是能进行模式补全的海马网络，冻结 LLM 也不是持续整合的新皮层；这个类比尚未决定实现。
- 再巩固支持“某些记忆提取后可被更新”，不支持“每次读取都必须持久写入”。短期推断状态改变、参数可塑性与原始档案追加，也必须分别观察。
- 一致且加强：讨论文档“原始痕迹永不删除”原本的理由是延迟理解；CLS 给出第二个理由：**慢学习者需要从情景存储回放来训练**，否则会遗忘（见问题四）。
- 新的启示：Kumaran 等指出“与已有结构一致的信息可以快速学会”，这为第九节“何时开始学习”提供了另一种机制：不只是二阶惊奇解除屏蔽，还有新信息与已有结构的契合度。
- 关于“刻意巩固容易偏离”：CLS 中的回放是可以按目标加权的，并不是无差别地重放；这与草籽的直觉并不冲突，但说明回放本身不必是“刻意巩固”，它可以由预测误差或目标驱动（推论）。

### 候选证伪实验

见问题四。

## 问题四：在线学习而不遗忘

**对应讨论文档**：十二节第 2、10 条；这是讨论中没有覆盖、但在工程上最现实的障碍。

### 已有答案

- **灾难性遗忘是稳健现象。** 神经网络依次学习多个任务时会覆盖旧知识。Parisi 等（2019，Neural Networks）的综述把生物系统中缓解它的因素归为结构可塑性、记忆回放、课程与迁移学习、内在动机和多感官整合。证据：**强**。
- **按重要性减慢学习。** EWC（Kirkpatrick 等，2017，PNAS）根据权重对旧任务的重要性减慢它们的学习，受新皮层突触巩固启发。证据：**中到强**。
- **生成式回放。** Shin 等（NIPS 2017）受海马生成性的启发，用“生成器 + 求解器”双模型，以生成的旧数据回放防止遗忘。证据：**中**。
- **先推断、后可塑。** Song 等（2024，Nature Neuroscience）在指定关联与连续学习模拟中比较 BP 的干扰与前瞻配置；后者先推断目标兼容的神经活动，再修改突触巩固，在这些设置中改善学习表现并减少干扰。证据：**中**（模拟及与实验数据的比较）；同一关联中不同权重更新的干扰与跨任务遗忘并非同一现象。

### 前提是否成立

柚子的编码器若在线训练，会面对非平稳的聊天流：话题、群体、说话方式随年份变化。灾难性遗忘几乎一定会出现。好在柚子有一个大多数持续学习研究没有的条件：**完整保存的原始档案**，可以做真实回放，而不必生成回放。

### 与讨论文档的对照

- 冲突（需要补入讨论）：讨论文档只考虑了“怎样学会新东西”，没有考虑“学新的时候怎样不忘旧的”。可训练编码器一旦引入，这就是首要工程问题。
- “先推断、后可塑”为研究读取与更新的耦合提供参照，但不等于每次读取必须学习。Song 等的前瞻配置在指定关联、多任务分类与控制实验中减少了干扰；它没有验证开放聊天中的事实纠错、时间顺序或长期检索，详见[问题八](#问题八局部误差怎样成为记忆的更新)。
- 一致：原始档案永不删除，在这里获得最实际的用途。

### 候选证伪实验

在冻结档案上按时间顺序训练编码器（问题一的设置），每隔一段时间测量它对早期月份的预测能力。比较无回放、从原始档案随机回放、按预测误差加权回放三种情况。若无回放也不遗忘，说明这个规模下问题不严重；若各种回放都无法阻止遗忘，说明需要更强的结构手段。

## 问题五：结构生长与盲区

**对应讨论文档**：第七节（盲区、测量是投影、测量函数从哪里来）；十二节第 4、8 条。

### 已有答案

- **过完备的稀疏编码。** Olshausen & Field（1996，Nature）：对自然图像学习稀疏线性编码，得到一整套局部化、有方向、带通的感受野，与初级视觉皮层相似；稀疏编码的输出统计独立性更高。证据：**强**。
- **匹配追踪。** Mallat & Zhang（1993，IEEE Transactions on Signal Processing）：从大字典中自适应地贪心选取原子，把信号分解为它们的线性组合。证据：**强**（数学方法，广泛使用）。
- **按误差阈值增加单元。** Draelos 等（2017）受成人海马神经发生启发，在自编码器层中，当样本的**重建误差**超过阈值时增加新节点，以获取新信息并保留旧表示。证据：**中**（MNIST 与 NIST 数据集）。
- **结构学习：约简。** Friston、Parr、Zeidman（2018）的贝叶斯模型约简能快速计算只在先验上不同的模型的证据，用于在大模型空间中做结构比较。它主要服务于**剪枝与比较**，而不是生长。证据：**中**。
- **知识即预测性问题。** Horde（Sutton 等，AAMAS 2011）由大量独立子 agent（demon）组成，每个回答一个关于世界的预测性或目标性问题（通用价值函数）。问题由设计者给定。

### 前提是否成立

生长方法（Draelos、匹配追踪）都以**相对于原始输入的残差**为触发条件，需要能计算原始输入的重建误差，或者至少存在一个能衡量原始输入的通道。

### 与讨论文档的对照

- 一致：“沿残差方向增加基函数”在匹配追踪与神经发生式生长中都有直接实现。
- **收窄初稿的必要性主张。** 这些重建型方法需要它们自己的残差；不能由此推出一切结构生长都必须重建原始输入。其它通道的失败、独立纠正或新观测也可能暴露表示遗漏。要发现已有表示丢掉的差异，至少需要不完全受该表示控制的检验通道；但它不必是原文生成器。原始事件 logprob 只是候选，而且一个总分既非“几乎不压缩的测量”，也不自动给出可生长的新方向。
- 一致：Horde 留下的“问题由谁提出”与讨论文档的“测量函数从哪里来”是同一个未解问题；结构学习方面成熟的工作偏向剪枝，生长仍不成熟。
- 盲区：已有工作没有提供在内部发现“完全没有接触的世界”的方法，这与讨论文档接受的哲学界限一致。

### 候选证伪实验

用问题一的编码器，比较“只看已有表示误差”与“允许一个独立检验通道指出遗漏”。例如旧表示把两类事件合并，但另一项可观测后果确实不同。检验候选新维度是否在留出未来保留了这个差异，并改善预测或使用；只有训练误差下降不算。原文 logprob 可以是其中一条实验臂，不预定为唯一生长来源。

## 问题六：可达性的度量

**对应讨论文档**：第六节“常应常静”、第九节“可达性作为统一判据”；十二节第 7 条。

### 已有答案

- **Empowerment。** Klyubin、Polani、Nehaniv（2005）把 empowerment 定义为 agent 执行通道的信息论容量，即 agent 原则上能在多大程度上改变世界；它与任务和动作的“意义”无关。证据：**中**（理论与模拟）。
- **反事实影响。** Jaques 等（ICML 2019）：每一步模拟自己本可以采取的其它动作，计算它们对其它 agent 行为的影响，影响大的动作得到奖励；作者证明这等价于奖励动作之间的高互信息。证据：**中**。
- **信息瓶颈。** Tishby、Pereira、Bialek（1999）：把“相关信息”定义为一个信号提供的关于另一个信号的信息，寻找保留关于另一变量最多信息的最短编码。证据：**强**（数学框架）。
- **从单通道重建状态。** Takens（1981）的延迟嵌入定理：在一般条件下，可以从一个可观测量的时间序列重建确定性动力系统的吸引子，重建保持微分同胚下不变的性质。证据：**强**（数学定理，前提严格）。
- **预测状态表示的充分性。** [Littman、Sutton、Singh（NIPS 2001）Theorem 1](https://papers.nips.cc/paper_files/paper/2001/file/1e4d36177d71bbb3558e43af9577d70e-Paper.pdf)：系统状态可以用多步、以动作为条件的对未来观测的预测来表示；具有有限状态 POMDP 表示的系统，其线性预测表示的维度可不超过该状态数。证据：**强**（条件性理论结果，不是开放世界有限表示的保证）。

### 前提是否成立

Empowerment 与信息瓶颈都需要估计互信息，在高维、样本少的情况下估计困难。Takens 的原定理依赖确定性与一般性条件，不能直接套到开放聊天流。柚子的离线分叉允许观察“如果所见输入不同，我的输出怎样变”，但不产生“如果动作不同，别人怎样回应”的真实反事实；后者需要新的交互证据或明确的环境模型。

### 与讨论文档的对照

- 方向相反但方法可借：empowerment 度量“行动 → 世界”的通道；草籽的可达性度量“世界 → 行动”的通道。两者都是信道容量或互信息问题。
- Jaques 等的[反事实影响方法](https://proceedings.mlr.press/v97/jaques19a.html)可启发成对比较，但论文估计“自身动作 → 他者行为”的影响，不是“世界变化 → 自身动作”的现成度量。换变量与方向后，概率对象、混杂与比较条件都要重新说明，不能直接继承其互信息解释。
- 一致：信息瓶颈对应“应＝充分，静＝最小”；PSR 的“预测数不超过最小状态数”是讨论文档第七节“充分性”的一个严格版本，但它要求系统本身有限，不能直接用于开放世界。
- 未找到：把“可达性”作为对一切（通道、行为、学习过程）的统一判据的已有工作。

### 候选证伪实验

在同一检查点做成对扰动，同时用同输入重复运行估计模型本身的随机差异；比较行动相关证据变化、同长度无关噪声与不改变语义的改写。link 可作一个已知依赖关系的对照，但不能因它结构简单就预设价值较低。若观察量无法区分上述已知差别，就没有得到可用的穿透度仪器；即使区分成功，也只证明对指定干预敏感，不证明“有用的可达性”已有普适总分。

## 问题七：预测与行动一体

**对应讨论文档**：第五节“行动是对观测的补全”、第八节预测编码机制。

### 已有答案

- **预测编码。** Rao & Ballard（1999，Nature Neuroscience）：高层向低层的反馈连接携带对低层活动的预测，前馈连接携带残差；在自然图像上训练后出现类似简单细胞的感受野，误差单元表现出末端抑制等经典感受野外效应。证据：**中到强**（模型解释了实验现象；作为皮层算法的证据仍有争议）。综述见 Keller & Mrsic-Flogel（2018，Neuron）、Friston（2018，Nature Neuroscience）。
- **局部学习与反向传播。** Whittington & Bogacz（2017）给出弱输出影响极限下的近似；Song 等（2020）的精确 Z-IL 依赖前向初始化与特殊时间调度；Millidge、Tschantz、Buckley（预印本 2020，正式 Neural Computation 2022）采用 fixed prediction 推广到可微计算图。证据：**强，但只对各自条件下的数学关系**；普通 PC 并不无条件等于 BP。详见[问题八](#问题八局部误差怎样成为记忆的更新)。
- **自由能框架。** Bogacz（2017，Journal of Mathematical Psychology）的教程逐步推导 Friston 的自由能框架，它扩展了 Rao & Ballard 的模型，学习由基于 Hebbian 的突触可塑性实现。证据：**中**（作为数学框架是严谨的；作为统一脑理论有争议）。
- **主动推断。** Friston 等（2017，Neural Computation）以自由能框架建立感知、策略推断与学习的过程理论，并用模拟解释若干神经与行为现象；“所有神经处理都如此”是理论的出发假设。其[§2 的模型](https://discovery.ucl.ac.uk/id/eprint/1530701/1/Friston_Active_Inference_Process_Theory.pdf)仍明确包含状态转移、动作与偏好先验，策略比较涉及期望自由能。暗室问题见 Friston、Thornton、Clark（2012）。证据：**弱到中**，不等于从普通预测准确度自动推出应做的动作。
- **世界模型。** Ha & Schmidhuber（2018）：VAE 把观测压缩为潜向量，MDN-RNN 预测未来潜状态，简单控制器可以完全在模型“想象”出的环境中训练。Dreamer（Hafner 等，2020）通过在学到的世界模型的紧凑状态空间里想象轨迹来学习长程行为。证据：**强**（多项基准上的结果）。
- **可验证原则。** Sutton（2001）《Verification, The Key to AI》：AI 系统只能在它能自己验证的范围内创造并维护知识。

### 前提是否成立

世界模型与 Dreamer 依赖可交互、可重置的环境和外部奖励；主动推断的完整形式需要显式的生成模型和偏好先验。柚子的环境不可重置，没有外部奖励；“偏好从哪里来”仍是讨论文档的开放问题。

### 与讨论文档的对照

- 主动推断为“行动参与预测”提供形式参照，但共享一个优化语言不要求抹去动作的因果地位或偏好。知道下一步最可能说什么，不等于知道应该说什么；这个缺口仍由讨论文档的“偏向从哪里来”承接。
- 世界模型在潜空间中想象与规划，支持“先压缩、再在压缩空间合成”这条可行路线；VAE 与 JEPA 的不同重建目标尚不能替柚子决定哪个更合适。
- Sutton 的可验证原则与保留外部检验一致，但“以后确实发生的事”只验证走过的预测。agent 的行动会改变后来观测，旧日志不能给出换一个动作后的结果；这是[同相上下文提案](self-maintaining-context.md#chatlog-是发生轨迹不是无偏地面真值)已保留的限制。
- 提醒：自由能框架作为统一理论的证据最弱，讨论文档应把它当作形式参照，不当作依据。

## 问题八：局部误差怎样成为记忆的更新

**对应讨论文档**：第四节的长跨度信用、第八节的局部学习、十二节第 10–13 条。本次题单最能补强的是“可训练层怎样更新”，而不是为整个记忆愿景提供一个现成算法。

### 局部规则成立在哪里

在一种固定精度的可微 PC 模型里，节点活动 `z`、预测函数 `f`、参数 `θ` 和各层残差共同定义能量：

```text
r_l = z_l - f_l(z_parents; θ_l)
E(z, θ) = ½ Σ_l r_lᵀ Π_l r_l
```

观测钳制某些节点，其余活动通过迭代减小 `E`；参数更新也沿同一能量的局部导数进行。“误差 × 活动”的 Hebbian 形状适用于相应参数线性连接，一般可微图还需要局部 Jacobian。[Bogacz 的教程](https://www.tnu.ethz.ch/fileadmin/user_upload/teaching/cpcourse/2020/Literature/Bogacz_2017.pdf)给出高斯模型下的推导，[任意计算图论文 §2](https://arxiv.org/abs/2006.04182)说明推广的条件。这里的“局部”是计算依赖局部；误差信息仍经过多步网络传播，不是只观察一个片段就自动知道它对整个未来的贡献。

至少三件事不能合称“读即写”：推断改变本次活动 `z`，学习改变跨次参数 `θ`，档案追加保存本次经历。前一件事可以发生而后两件事不发生；同一能量也没有要求每次检索永久更新。文本片段由冻结 LLM 改写，不因此获得 `E`、梯度或可用的局部信用信号。

### 近似、精确与调度的区别

| 机制 | 关键条件与实际结论 | 不能省略的代价 |
|---|---|---|
| 普通 PC 与 BP 接近 | Whittington & Bogacz（2017）减小输出误差对隐活动的影响，使平衡活动接近前向值；误差同时缩小，需补偿学习率 | 这是带尺度条件的极限，不是任意目标钳制都精确；低幅误差信号也有生物编码问题 |
| fixed prediction | Millidge 等（2022）、Rosenbaum（2022）在前向初始化后固定预测及求导位置；平衡递推给出 BP 梯度，有限步通常近似 | 动力学已改变；Rosenbaum 更正版算法在前向初态、步长 1、规定顺序与至少 L 步（L 为网络深度）下精确，并可化简为直接 BP |
| Z-IL | Song 等（2020）：前向初始化、内部初始误差零、积分步长 1、各层仅在误差到达的指定时刻更新；Salvatori 等（2022）用恒等节点把一般可微 DAG 分层并同步路径 | 精确性来自初始化、图变换与时间调度；不是等活动平衡后的普通 PC，也不证明脑具有这些同步条件 |
| iPC | Salvatori 等（ICLR 2024）在每一步同时更新活动与权重，取消两阶段切换 | 仍有学习率、步数与收敛问题；不能把它写成严格“先推断、后可塑”的另一名字 |

表中来源与算法定位见文末[题单](#预测编码题单与版本核对)。Rosenbaum 的[2025 正式更正](https://doi.org/10.1371/journal.pone.0320944)修订了误差更新、算法与证明公式，应与原文一起读。

**收敛也有不同含义。** [Millidge 等（ICLR 2023）Theorem 3.6](https://arxiv.org/pdf/2207.12316)要求足够小的学习率、每批初态满足损失／残差梯度关系，并假定推断收敛；证明的单调性论证采用连续时间／无穷小步长，得到 BP loss 的临界点结论，不是全局最优或任意有限步保证。“放宽生物约束”的论文则主要报告指定分类任务仍可训练，三项约束同时放宽时 ReLU 会不稳定；它不是放宽后仍精确 BP 的证明。

**效率没有普适赢家。** [iPC 论文 §3.1、Table 3 与附录 C](https://proceedings.iclr.cc/paper_files/paper/2024/file/554414e570a85eb3118e988c5d77986f-Paper-Conference.pdf)支持它相对标准 PC 的稳定性和效率改善；部分效率比较采用不可并行矩阵乘次数并依赖并行假设。轻量语言模型实验中，iPC 在 masked 任务的 perplexity 优于 BP，在 conditional 任务则更差，后者 10 次运行只有 7 次收敛。“局部更新”不等于在个人电脑上更便宜。

### 神经证据与记忆迁移

[Rao & Ballard（1999）](https://doi.org/10.1038/4580)主要是计算模型重现部分视觉现象；[Keller & Mrsic-Flogel（2018）](https://doi.org/10.1016/j.neuron.2018.10.003)明确列出还需辨别的内部表征与电路实验。观察到失配响应，不能唯一证明误差就在该处计算。Friston 的 2018 文章是 News & Views；数学等价、模拟解释、局部神经证据与整个脑的实现应分开。

[理论与实验综述 §4.5、§5](https://arxiv.org/html/2107.12979v3)仍把复杂动作规划、工作记忆和海马相关长期记忆列为不足。PC 联想记忆中的典型任务是从受损提示恢复存储样本，这不等于在多年聊天中恢复时间、来源、纠错与承诺。Song 等（2024）的前瞻配置支持研究“先协调目标兼容活动再巩固参数”，但没有填上这项迁移缺口。

### 对柚子的候选实验

尚未运行；合成例子只校准仪器，不把少数任务定义为通用 agent 的目标，也不替换[实践反馈主线](memory-replay-evaluation.md#实践反馈循环)。

1. **先问表示是否有用。** 冻结编码器和目标，用过去预测留出的后续表示，与无记忆、打乱／错配记忆、廉价直接检索作等预算比较；目标在当时不可见。若真实记忆无增益，先检查预测对象、寻址和表示，不先换优化器。
2. **再问 PC 是否增加能力。** 同一可训练小层、数据与损失，对比 BP 和指定 PC 变体，计入全部推断步、墙钟成本、旧材料表现和表示漂移。冻结主 LLM 不妨碍给小层使用 BP；PC 必须用独有收益证明额外推断成本。
3. **最后问预测增益是否进入行为。** 让后续切片实际消费前一步产生的选择或结构，与不消费分支比较取证、纠错、任务接续和成本。预测改善但这些不改善，说明预测目标尚未足以代理记忆的功能，不能自动晋级。

## 我们自己的数据：旧试验品的回放实验

[长期记忆离线回放实验](memory-replay-evaluation.md)虽然测的是旧设计，但它是本项目唯一的一手数据。按本文的问题重读（以下各条的“含义”均为推论）：

| 观察 | 对新框架的含义 |
|---|---|
| 默认提示下模型会覆盖但不递归整理，摘要随阅读线性增长，prompt 达约 127k | 否定这次基线会自行控制增长；其它提示曾形成递归层级，故不能推成主 LLM 在线维护一律不可行 |
| 有可达路径但检索变成大范围枚举，12 次行动只有 3 次在正确路径上 | 需要更便宜的寻址；编码器是候选，语义下一跳、普通检索或查询规则也是候选 |
| 内联直接成员号使定向检索收敛，但形成成本约翻倍 | 指定索引与读取指导有效但昂贵；尚未比较它与编码器，不足以决定交接职责 |
| 一次“只判真假”的核验也会消耗数千 completion token | 用 LLM 调用逐条充当测量函数代价很高，支持小编码器 |
| 长思考经常以 `length` 截断，整理与聊天争夺同一上下文 | 与讨论文档“维护与行动争夺注意力”一致 |

## 横向结论

1. **已有工作提供多个受条件约束的机制，而非完整拼装证明。** 表示预测、局部可塑性、回放与不确定性学习各有依据；把它们统一到冻结 LLM 的文本结构上仍是本项目的假设。
2. **二阶预测与可达性还没有操作定义。** 本次检索未找到直接证明“统一用于一切”的工作；检索未命中不构成新颖性证据。精度、分歧、学习结果预测以及行动敏感度不能因名字相近而互换。
3. **持续学习是重要新压力。** 需要同时检查遗忘与表示坐标漂移；原始档案支持回放，却不自动完成模式补全、结构学习或反事实评价。
4. **盲区需要独立检验，不必预定原始重建。** 一个不完全受当前编码器控制的通道才能指出其遗漏；原文 logprob 既非唯一选择，也不自动定位新维度。
5. **PC–BP 的条件性数学结果较强，完整神经实现与聊天迁移的证据仍弱。** 活动推断、参数学习和经历保存要分别命名；同一误差语言不保证相同计算、同一调度或同等成本。
6. **预测好并不自动等于记忆有用。** 可预测的承诺仍需要及时进入行动；不可压缩的独特事实仍可能必要；动作会改变后续数据。偏好、独立检验与实际消费仍须保留。

## 对讨论文档的影响

VICReg 更正、互补学习系统与灾难性遗忘已在讨论中登记。2026-10-08 的[续思](memory-foundations-discussion.md#十三2026-10-08文献核对后的续思)进一步处理预测对象、二阶惊奇、可达性与局部信用的缺口；这些是研究推论，没有替维护者批准编码器、PC 网络或运行时改造。

## 参考文献

以下其它领域沿用初稿的摘要级核对；本次补读范围见开头说明。预测编码的 20 项题单另列读取层级与版本，避免把不同版本当成独立证据。

**编码器与表示学习**

- Assran, M. 等 (2023). [Self-Supervised Learning from Images with a Joint-Embedding Predictive Architecture](https://openaccess.thecvf.com/content/CVPR2023/html/Assran_Self-Supervised_Learning_From_Images_With_a_Joint-Embedding_Predictive_Architecture_CVPR_2023_paper.html). CVPR.
- Bardes, A., Ponce, J., & LeCun, Y. (2022). [VICReg: Variance-Invariance-Covariance Regularization for Self-Supervised Learning](https://arxiv.org/abs/2105.04906). ICLR.
- LeCun, Y. (2022). A Path Towards Autonomous Machine Intelligence. OpenReview 立场文件.
- van den Oord, A., Li, Y., & Vinyals, O. (2018). [Representation Learning with Contrastive Predictive Coding](https://arxiv.org/abs/1807.03748). arXiv.

**好奇、精度与不确定性**

- Burda, Y. 等 (2018). [Large-Scale Study of Curiosity-Driven Learning](https://arxiv.org/abs/1808.04355). arXiv / ICLR 2019.
- Burda, Y. 等 (2018). [Exploration by Random Network Distillation](https://arxiv.org/abs/1810.12894). arXiv / ICLR 2019.
- Feldman, H., & Friston, K. (2010). [Attention, uncertainty, and free-energy](https://www.frontiersin.org/articles/10.3389/fnhum.2010.00215/full). Frontiers in Human Neuroscience.
- Kendall, A., & Gal, Y. (2017). [What Uncertainties Do We Need in Bayesian Deep Learning for Computer Vision?](https://arxiv.org/abs/1703.04977) NeurIPS.
- Kidd, C., Piantadosi, S. T., & Aslin, R. N. (2012). [The Goldilocks effect: human infants allocate attention to visual sequences that are neither too simple nor too complex](https://doi.org/10.1371/journal.pone.0036399). PLoS ONE.
- Mathys, C., Daunizeau, J., Friston, K. J., & Stephan, K. E. (2011). [A Bayesian foundation for individual learning under uncertainty](https://www.frontiersin.org/articles/10.3389/fnhum.2011.00039/full). Frontiers in Human Neuroscience.
- Oudeyer, P.-Y., Kaplan, F., & Hafner, V. V. (2007). [Intrinsic Motivation Systems for Autonomous Mental Development](https://infoscience.epfl.ch/record/115468). IEEE Transactions on Evolutionary Computation.
- Pathak, D., Gandhi, D., & Gupta, A. (2019). [Self-Supervised Exploration via Disagreement](https://arxiv.org/abs/1906.04161). ICML.
- Schmidhuber, J. (2008). [Driven by Compression Progress](https://arxiv.org/abs/0812.4360). arXiv.
- Schmidhuber, J. (2010). Formal Theory of Creativity, Fun, and Intrinsic Motivation (1990–2010). IEEE Transactions on Autonomous Mental Development.

**互补学习系统、回放与再巩固**

- Kumaran, D., Hassabis, D., & McClelland, J. L. (2016). [What Learning Systems do Intelligent Agents Need? Complementary Learning Systems Theory Updated](https://doi.org/10.1016/j.tics.2016.05.004). Trends in Cognitive Sciences.
- McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C. (1995). [Why there are complementary learning systems in the hippocampus and neocortex](https://pubmed.ncbi.nlm.nih.gov/7624455/). Psychological Review.
- Nader, K., Schafe, G. E., & LeDoux, J. E. (2000). Fear memories require protein synthesis in the amygdala for reconsolidation after retrieval. Nature.
- Wilson, M. A., & McNaughton, B. L. (1994). [Reactivation of hippocampal ensemble memories during sleep](https://pubmed.ncbi.nlm.nih.gov/8036517/). Science.

**持续学习**

- Kirkpatrick, J. 等 (2017). [Overcoming catastrophic forgetting in neural networks](https://pmc.ncbi.nlm.nih.gov/articles/PMC5380101). PNAS.
- Parisi, G. I., Kemker, R., Part, J. L., Kanan, C., & Wermter, S. (2019). [Continual lifelong learning with neural networks: A review](https://arxiv.org/abs/1802.07569). Neural Networks.
- Shin, H., Lee, J. K., Kim, J., & Kim, J. (2017). [Continual Learning with Deep Generative Replay](https://arxiv.org/abs/1705.08690). NIPS.
- Song, Y., Millidge, B., Salvatori, T., Lukasiewicz, T., Xu, Z., & Bogacz, R. (2024). [Inferring neural activity before plasticity as a foundation for learning beyond backpropagation](https://doi.org/10.1038/s41593-023-01514-1). Nature Neuroscience.

**结构生长**

- Draelos, T. J. 等 (2017). [Neurogenesis Deep Learning](https://arxiv.org/abs/1612.03770). arXiv.
- Friston, K., Parr, T., & Zeidman, P. (2018). [Bayesian model reduction](https://arxiv.org/abs/1805.07092). arXiv.
- Mallat, S., & Zhang, Z. (1993). Matching Pursuits with Time-Frequency Dictionaries. IEEE Transactions on Signal Processing.
- Olshausen, B. A., & Field, D. J. (1996). [Emergence of simple-cell receptive field properties by learning a sparse code for natural images](https://doi.org/10.1038/381607a0). Nature.
- Sutton, R. S. 等 (2011). Horde: A Scalable Real-time Architecture for Learning Knowledge from Unsupervised Sensorimotor Interaction. AAMAS.

**可达性、信息与充分性**

- Jaques, N. 等 (2019). [Social Influence as Intrinsic Motivation for Multi-Agent Deep Reinforcement Learning](https://proceedings.mlr.press/v97/jaques19a.html). ICML.
- Klyubin, A. S., Polani, D., & Nehaniv, C. L. (2005). [Empowerment: A Universal Agent-Centric Measure of Control](https://doi.org/10.1007/11553090_75). ECAL.
- Littman, M. L., Sutton, R. S., & Singh, S. (2001). [Predictive Representations of State](https://papers.nips.cc/paper/2001/hash/1e4d36177d71bbb3558e43af9577d70e-Abstract.html). NIPS.
- Takens, F. (1981). Detecting strange attractors in turbulence. Lecture Notes in Mathematics.
- Tishby, N., Pereira, F. C., & Bialek, W. (1999). [The information bottleneck method](https://arxiv.org/abs/physics/0004057). Allerton Conference.

### 预测编码题单与版本核对

按维护者给出的题单顺序列全 20 项。**正文关键节**＝取得论文原文并核读本次结论依赖的章节、方法或证明，不等于逐页通读；**摘要／元数据**＝没有据此验证正文机制。链接优先正式出处；需要阅读开放原文时同时列作者稿／预印本。

| # | 文献、作者与出处 | 本次读取及用途边界 |
|---|---|---|
| 1 | Bogacz（2017），[A tutorial on the free-energy framework for modelling perception and learning](https://doi.org/10.1016/j.jmp.2015.11.003)，Journal of Mathematical Psychology 76:198–211；[开放原文](https://www.tnu.ethz.ch/fileadmin/user_upload/teaching/cpcourse/2020/Literature/Bogacz_2017.pdf) | 正文 §2–5：推断、权重及方差学习；数学教程，不是完整脑理论的实验证明 |
| 2 | Friston（2018），[Does predictive coding have a future?](https://www.nature.com/articles/s41593-018-0200-7)，Nature Neuroscience 21:1019–1021 | 元数据、导言与作者稿开头；News & Views，全文获取失败，不能当作新实验 |
| 3 | Huang & Rao（2011），[Predictive coding](https://doi.org/10.1002/wcs.142)，WIREs Cognitive Science 2:580–593；[作者原文](https://homes.cs.washington.edu/~rao/predcoding2011.pdf) | 正文及结论：早期视觉与高层皮层证据有差别；本题不是另一篇近年的动态预测编码论文 |
| 4 | Keller & Mrsic-Flogel（2018），[Predictive Processing: A Canonical Cortical Computation](https://doi.org/10.1016/j.neuron.2018.10.003)，Neuron 100:424–435；[作者稿](https://discovery.ucl.ac.uk/id/eprint/10064516/3/Keller_Mrsic-Flogel.pdf) | 正文电路证据与“THE EXPERIMENTS THAT NEED TO BE DONE”：保留能区分理论的实验缺口 |
| 5 | Lillicrap、Santoro、Marris、Akerman & Hinton（2020），[Backpropagation and the brain](https://doi.org/10.1038/s41583-020-0277-3)，Nature Reviews Neuroscience 21:335–346；[作者原文](https://www.cs.toronto.edu/~hinton/absps/backpropandbrain.pdf) | 正文 NGRAD 与结论：活动差编码教学信号是候选共同原则，脑实际算法未定 |
| 6 | Marino（2022；预印本 2020），[Predictive Coding, Variational Autoencoders, and Biological Connections](https://doi.org/10.1162/neco_a_01458)，Neural Computation 34:1–44；[预印本](https://arxiv.org/abs/2011.07464) | 正文 §3–6：迭代潜变量推断与摊销推断的比较；提出的生物对应未获统一实证确认 |
| 7 | Millidge、Salvatori、Song、Bogacz & Lukasiewicz（2022），[Predictive Coding: Towards a Future of Deep Learning beyond Backpropagation?](https://www.ijcai.org/proceedings/2022/774)，IJCAI Survey Track:5538–5545 | 正文 §2–4：标准 PC、BP 条件与查询灵活性；综述展望不是普适性能证明 |
| 8 | Millidge、Seth & Buckley（2021；v3 2022），[Predictive Coding: a Theoretical and Experimental Review](https://arxiv.org/abs/2107.12979)；[v3 正文](https://arxiv.org/html/2107.12979v3) | 正文 §2.4、§4.5、§5；本次核实出处为预印本，保留复杂规划与长期记忆的不足 |
| 9 | Millidge、Song、Salvatori、Lukasiewicz & Bogacz（ICLR 2023；预印本 2022），[A Theoretical Framework for Inference and Learning in Predictive Coding Networks](https://openreview.net/forum?id=ZCTvSF_uVM4)；[预印本正文](https://arxiv.org/pdf/2207.12316) | 预印本 Theorems 3.3、3.6 与证明：可逆条件／小步长、初态条件、推断收敛；正式 PDF 遇验证，未做两版逐字比较 |
| 10 | Millidge、Tschantz & Buckley（2022；预印本 2020），[Predictive Coding Approximates Backprop along Arbitrary Computation Graphs](https://doi.org/10.1162/neco_a_01497)，Neural Computation 34:1329–1368；[预印本](https://arxiv.org/abs/2006.04182) | 正文 §2、算法 1：fixed prediction、前向初态与平衡条件；循环模型使用展开图，不是未经处理的任意环 |
| 11 | Millidge、Tschantz、Seth & Buckley（2020），[Relaxing the Constraints on Predictive Coding Models](https://arxiv.org/abs/2010.01047) | 正文 §3、Discussion；本次核实出处为预印本。独立反馈、去导数、稠密误差连接的分类实验；组合松弛有不稳定例外 |
| 12 | Rao & Ballard（1999），[Predictive coding in the visual cortex: a functional interpretation of some extra-classical receptive-field effects](https://doi.org/10.1038/4580)，Nature Neuroscience 2:79–87；[开放原文](https://ni.cmu.edu/~tai/microns_papers/rao_ballard.pdf) | 正文模型、模拟与 Discussion：解释部分感受野外效应，不排除其它回路解释 |
| 13 | Rosenbaum（2022），[On the relationship between predictive coding and backpropagation](https://doi.org/10.1371/journal.pone.0266102)，PLOS ONE 17:e0266102；[修订预印本 v6](https://arxiv.org/abs/2106.13082v6)；[2025 正式更正](https://doi.org/10.1371/journal.pone.0320944) | 正文算法 2–4、Theorem 1、Discussion 及更正；严格 PC 与 fixed prediction 分开。不能沿用 v6 首页“更正尚未发表”的历史提示 |
| 14 | Salvatori、Mali、Buckley、Lukasiewicz、Rao、Friston & Ororbia，题单名 *A Survey on Brain-Inspired Deep Learning via Predictive Coding*；[2023 起预印本版本链](https://arxiv.org/abs/2308.07870)，v3（2025）题名 *Brain-inspired Computational Intelligence via Predictive Coding*；正式版（2026）[A survey on neuro-mimetic deep learning via predictive coding](https://doi.org/10.1016/j.neunet.2025.108161)，Neural Networks 195:108161 | v3 正文 §3–4，正式版仅元数据／摘要；同一作品改题与更新，不是三项独立验证。深层扩展、空间成本与联想记忆任务范围仍需留意 |
| 15 | Salvatori、Song、Xu、Lukasiewicz & Bogacz（2022），[Reverse Differentiation via Predictive Coding](https://doi.org/10.1609/aaai.v36i7.20788)，AAAI 36:8150–8158；[正式原文](https://ojs.aaai.org/index.php/AAAI/article/download/20788/20547) | 正文 identity vertices、levelled DAG、算法 2–3、Theorems 3–4：图变换同步路径后扩展精确 Z-IL |
| 16 | Salvatori、Song、Yordanov、Millidge、Emde、Xu、Sha、Bogacz & Lukasiewicz（ICLR 2024；预印本 2022），[A Stable, Fast, and Fully Automatic Learning Algorithm for Predictive Coding Networks](https://proceedings.iclr.cc/paper_files/paper/2024/file/554414e570a85eb3118e988c5d77986f-Paper-Conference.pdf) | 正文 §3、Table 3、附录 C/D：iPC 同步更新活动与参数；相对标准 PC 有改善，相对 BP 结果混合 |
| 17 | Song、Lukasiewicz、Xu & Bogacz（2020），[Can the Brain Do Backpropagation? — Exact Implementation of Backpropagation in Predictive Coding Networks](https://papers.neurips.cc/paper_files/paper/2020/hash/fec87a37cdeec1c6ecf8181c0aa2d3bf-Abstract.html)，NeurIPS 33；[正式原文](https://proceedings.neurips.cc/paper/2020/file/fec87a37cdeec1c6ecf8181c0aa2d3bf-Paper.pdf) | 正文 §3–4、§6，C1–C3 与 Theorems 3.1–3.2：Z-IL 精确参数更新；仍有对称反馈和非脉冲神经元等限制 |
| 18 | Song、Millidge、Salvatori、Lukasiewicz、Xu & Bogacz（2024），[Inferring neural activity before plasticity as a foundation for learning beyond backpropagation](https://doi.org/10.1038/s41593-023-01514-1)，Nature Neuroscience 27:348–358；[机构原文](https://repositum.tuwien.at/bitstream/20.500.12708/193037/1/Song-2024-Nature%20Neuroscience-vor.pdf) | Results 与 Methods：前瞻配置、有限 relaxation 与分类／控制实验；没有开放聊天长期记忆验证 |
| 19 | Whittington & Bogacz（2019），[Theories of Error Back-Propagation in the Brain](https://doi.org/10.1016/j.tics.2018.12.005)，Trends in Cognitive Sciences 23:235–250；[机构原文](https://www.mrcbndu.ox.ac.uk/sites/default/files/reprint_backprop_review.pdf) | 摘要、开头与生物限制相关正文；未逐节比较全部模型。局部误差、权重对称和神经元实现仍是问题 |
| 20 | Whittington & Bogacz（2017），[An Approximation of the Error Backpropagation Algorithm in a Predictive Coding Network with Local Hebbian Synaptic Plasticity](https://doi.org/10.1162/NECO_a_00949)，Neural Computation 29:1229–1262；[开放原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC5467749/) | 正文 §3.1.2、§4：弱输出影响和学习率补偿的渐近关系；输出仍可硬钳制，不能仅凭“弱钳制”三个字复述条件 |

视频：[Artem Kirsanov, The Brain’s Learning Algorithm Isn’t Backpropagation](https://www.youtube.com/watch?v=l-OLgbdZ3kk)。已取得 YouTube 官方元数据与描述（当前描述注明 2026-02 重整），未观看、未取得字幕，未核实原始发布日期。它提供学习入口与题单，论文结论由上面的原文支持，不引用视频中未核实的论证。

若从机制入手，先读 Bogacz，再读 Whittington & Bogacz（2017）与 Rosenbaum 及更正；随后对照 Z-IL、前瞻配置与 iPC 的不同调度，最后回到两篇综述核对未解问题。PC 与 VAE 的比较帮助判断反复推断的成本能否由摊销降低，不意味着必须把柚子改成生成式架构。

### 主动推断与世界模型

- Friston, K., FitzGerald, T., Rigoli, F., Schwartenbeck, P., & Pezzulo, G. (2017). [Active Inference: A Process Theory](https://doi.org/10.1162/NECO_a_00912). Neural Computation.
- Friston, K., Thornton, C., & Clark, A. (2012). [Free-energy minimization and the dark-room problem](https://doi.org/10.3389/fpsyg.2012.00130). Frontiers in Psychology.
- Ha, D., & Schmidhuber, J. (2018). [World Models](https://arxiv.org/abs/1803.10122). arXiv.
- Hafner, D. 等 (2020). [Dream to Control: Learning Behaviors by Latent Imagination](https://arxiv.org/abs/1912.01603). ICLR.
- Sutton, R. S. (2001). Verification, The Key to AI. 个人网站短文.

**其它**

- Feynman, R. P., & Leighton, R. *What Do You Care What Other People Think?*，“It's as Simple as One, Two, Three”一章：费曼计数时在心里“说”，因而不能说话；Tukey 在心里“看”一条写着数字的带子，因而不能读书。
