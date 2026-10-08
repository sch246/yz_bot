# 记忆基础问题综述：按开放问题组织

> 状态：外部研究综述（2026-10-06）。它按[基础问题讨论](memory-foundations-discussion.md)留下的开放问题组织已有工作，不是运行合同，不批准实现，也不替讨论文档下结论。

## 为什么写、怎样读

[讨论文档](memory-foundations-discussion.md)形成了一组工作假设：预测统一写入、读取与学习；行动是对观测的补全；误差落在编码器（比喻地说，测量函数）的输出上；二阶惊奇处理噪声；无常的可达性是统一判据。讨论后期，这些思路频繁与现成领域重合，且一度依赖约 15 条凭记忆的引用。本文的目的有两个：核对这些引用，并找出已有工作对每个开放问题给出了什么。

旧的[记忆与自我演化研究地图](memory-and-self-evolution-research.md)覆盖的是 agent 记忆文献（MemGPT、RAPTOR、Reflexion 等），对应旧问题框架。本文覆盖计算神经科学与表示学习，两者互补，不替代。

**按问题组织，而不是按领域组织。** 草籽担心少数例子或某个领域的框架会把目标收窄，所以每个问题只回答四件事：

1. 已有工作给出了什么答案，证据有多强；
2. 它依赖哪些前提，这些前提在柚子身上是否成立；
3. 与讨论文档的判断哪里一致、哪里冲突；
4. 能推翻我们假设的最小实验是什么（只是候选，是否运行由维护者决定）。

**证据强度标记**：**强**＝多项独立实验或数学证明；**中**＝特定任务上的实验结果；**弱**＝理论主张、类比或尚有争议。

**核对方式与边界。** 本会话的网络策略阻止访问 arXiv 与 YouTube。下文文献的标题、作者、年份、出处和摘要级要点均已通过网页搜索核对；**没有通读原文**。超出摘要的机制细节，以及向柚子的迁移，都标为“推论”。

## 问题一：编码器的训练目标与防坍缩

**对应讨论文档**：十二节第 2、3 条（编码器形态与训练目标；测量空间中的合成还是纯选择）。

### 已有答案

- **在表示空间预测，而不是在原始数据上生成。** LeCun 的立场文件《A Path Towards Autonomous Machine Intelligence》（2022，OpenReview）提出 JEPA：预测目标的表示而非目标本身，目标编码器可以丢掉不相关的细节（纹理、噪声），使表示更抽象、更可预测；它要求表示“同时最大化信息量与可预测性”。I-JEPA（Assran 等，CVPR 2023）是图像上的实例：从一个上下文块预测其它目标块的表示，不做像素重建。证据：**中**（视觉下游任务表现强，但目标是表示质量，不是持续学习）。
- **对比式预测：选择，而不是生成。** CPC（van den Oord、Li、Vinyals，2018）用自回归模型在潜空间预测未来，以概率对比损失（InfoNCE，一个 N 选 1 分类）训练，使潜空间保留对预测未来最有用的信息；在语音、图像、文本和 3D 强化学习上有效。证据：**中**。这就是讨论文档所说的“纯选择”。
- **防坍缩。** VICReg（Bardes、Ponce、LeCun，ICLR 2022）用两个正则项显式防止编码器输出常数或无信息向量：方差项让每个维度的方差保持在阈值之上；协方差项让每对维度去相关。证据：**中**。

### 前提是否成立

这些方法都靠梯度端到端训练编码器，并依赖大量同分布数据（如数据增强产生的同一图像的不同视图）。柚子的事件流量小、非平稳，而且编码器要在线更新（见问题四）。“预测表示而非原文”的核心理由（原文细节大多不可预测）在聊天文本上同样成立；数据量与在线性两条前提不成立或尚未知。

### 与讨论文档的对照

- 一致：不为比较 4k 内容而生成 4k 内容的成本论证，正是 JEPA 与 CPC 的出发点。
- **需要更正**：讨论文档第八节把 VICReg 的第三项写成“不变性（预测）项：预测要准”。核对后，VICReg 的不变性项是同一样本两个视图的嵌入之间的一致性（摘要级核对确认了方差项与协方差项；第三项的具体形式属于原文细节，未通读）。它与“预测”只在 JEPA 式设置中才对应，不应直接等同。方差项对应“应”、协方差项对应冗余与正交性，这两条对应仍成立。
- 未核对：I-JEPA 具体如何防坍缩（通常认为依赖非对称结构与目标编码器的滑动平均），摘要级材料未说明。

### 候选证伪实验

在冻结档案上，用现成的句向量模型得到事件表示，训练一个小预测器“从前文预测下一事件的表示”。比较有无方差／协方差项时，表示维度是否坍缩，以及预测增益是否只是来自坍缩。若在这个数据量下不加正则也不坍缩，或加了也坍缩，“编码器 + 防坍缩”的路线就需要重新评估。

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

- 一致且得到支持：草籽的“二阶惊奇”有两个独立的现成实现。**集成分歧**（Pathak 等）几乎是它的直接版本：对噪声，多个模型都收敛到同一个平均预测，彼此一致，于是没有奖励；对尚未理解的结构，模型之间分歧大。层级高斯滤波器则是“预测误差的方差，再预测方差的变化”的形式化版本。
- 一致：“噪声与未理解之物只能在时间中区分”对应认知不确定性可随数据消除、偶然不确定性不能。
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

- **这是讨论中最大的遗漏。** “LLM 黑盒 + 可训练编码器 + 原始档案”的结构，几乎就是 CLS 的工程版本，而讨论中从未提到。
- 一致：“读即写”有直接的生物证据，即再巩固。
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
- **先推断、后可塑。** Song 等（2024，Nature Neuroscience）指出反向传播会让新旧信息发生灾难性干扰；“前瞻配置”先推断学习之后应有的神经活动，再修改突触去巩固这一变化，在生物面对的许多情境中学习更高效、干扰更少。证据：**中**（模拟与与实验数据的比较）。

### 前提是否成立

柚子的编码器若在线训练，会面对非平稳的聊天流：话题、群体、说话方式随年份变化。灾难性遗忘几乎一定会出现。好在柚子有一个大多数持续学习研究没有的条件：**完整保存的原始档案**，可以做真实回放，而不必生成回放。

### 与讨论文档的对照

- 冲突（需要补入讨论）：讨论文档只考虑了“怎样学会新东西”，没有考虑“学新的时候怎样不忘旧的”。可训练编码器一旦引入，这就是首要工程问题。
- 一致：“读即写”与“先推断、后可塑”同构，而后者恰好被认为能减少干扰。
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
- 冲突与澄清：这些方法的残差都来自**重建**，也就是合成。讨论文档第八节选择了“测量而不是生成”。两者的调和是讨论文档已经提出的“几乎不压缩的原始测量”兜底：只在最底层保留一个能衡量原始输入的通道（例如 LLM 对原始事件的条件 logprob），用它产生生长所需的残差。本文的核对说明，这个兜底不是可选项，而是生长机制的前提（推论）。
- 一致：Horde 留下的“问题由谁提出”与讨论文档的“测量函数从哪里来”是同一个未解问题；结构学习方面成熟的工作偏向剪枝，生长仍不成熟。
- 盲区：已有工作没有提供在内部发现“完全没有接触的世界”的方法，这与讨论文档接受的哲学界限一致。

### 候选证伪实验

用问题一的编码器，在原始事件的 logprob 残差超过阈值时增加一个维度，检验新维度是否在留出的未来数据上带来预测增益，还是只拟合了噪声。若新维度普遍没有留出增益，残差驱动的生长在这个数据量下不可用。

## 问题六：可达性的度量

**对应讨论文档**：第六节“常应常静”、第九节“可达性作为统一判据”；十二节第 7 条。

### 已有答案

- **Empowerment。** Klyubin、Polani、Nehaniv（2005）把 empowerment 定义为 agent 执行通道的信息论容量，即 agent 原则上能在多大程度上改变世界；它与任务和动作的“意义”无关。证据：**中**（理论与模拟）。
- **反事实影响。** Jaques 等（ICML 2019）：每一步模拟自己本可以采取的其它动作，计算它们对其它 agent 行为的影响，影响大的动作得到奖励；作者证明这等价于奖励动作之间的高互信息。证据：**中**。
- **信息瓶颈。** Tishby、Pereira、Bialek（1999）：把“相关信息”定义为一个信号提供的关于另一个信号的信息，寻找保留关于另一变量最多信息的最短编码。证据：**强**（数学框架）。
- **从单通道重建状态。** Takens（1981）的延迟嵌入定理：在一般条件下，可以从一个可观测量的时间序列重建确定性动力系统的吸引子，重建保持微分同胚下不变的性质。证据：**强**（数学定理，前提严格）。
- **预测状态表示的充分性。** Littman、Sutton、Singh（NIPS 2001）：系统状态可以用多步、以动作为条件的对未来观测的预测来表示；任何系统都有一个线性预测状态表示，其预测数不超过最小 POMDP 模型的状态数。证据：**强**（理论结果）。

### 前提是否成立

Empowerment 与信息瓶颈都需要估计互信息，在高维、样本少的情况下估计困难。Takens 定理要求确定性与低噪声，聊天流不满足。反事实影响的测量需要能重放“如果输入不同”，这一点柚子的离线回放基础设施恰好具备。

### 与讨论文档的对照

- 方向相反但方法可借：empowerment 度量“行动 → 世界”的通道；草籽的可达性度量“世界 → 行动”的通道。两者都是信道容量或互信息问题。
- **最直接的可用工具**是 Jaques 等的反事实影响：扰动一个输入，观察行动分布的变化，并且有互信息的理论解释。它正是讨论文档第六节提出的“扰动重放度量穿透度”的现成版本，只是把“其它 agent 的动作”换成“世界的变化”。
- 一致：信息瓶颈对应“应＝充分，静＝最小”；PSR 的“预测数不超过最小状态数”是讨论文档第七节“充分性”的一个严格版本，但它要求系统本身有限，不能直接用于开放世界。
- 未找到：把“可达性”作为对一切（通道、行为、学习过程）的统一判据的已有工作。

### 候选证伪实验

在冻结档案的回放中，对同一检查点做成对扰动（改动一条消息、删去一条消息、替换为同长度噪声），测量中心 agent 行动的变化，并与一条只匹配固定条件的 link 对照。若度量无法区分 link（应接近只有一缕无常可达）与中心 agent，或对噪声替换与语义改动给出同样的变化，这个度量就没有辨别力。

## 问题七：预测与行动一体

**对应讨论文档**：第五节“行动是对观测的补全”、第八节预测编码机制。

### 已有答案

- **预测编码。** Rao & Ballard（1999，Nature Neuroscience）：高层向低层的反馈连接携带对低层活动的预测，前馈连接携带残差；在自然图像上训练后出现类似简单细胞的感受野，误差单元表现出末端抑制等经典感受野外效应。证据：**中到强**（模型解释了实验现象；作为皮层算法的证据仍有争议）。综述见 Keller & Mrsic-Flogel（2018，Neuron）、Friston（2018，Nature Neuroscience）。
- **局部学习近似反向传播。** Whittington & Bogacz（2017，Neural Computation）：预测编码网络只用局部 Hebbian 可塑性就能自主完成监督学习，特定参数下权重更新收敛到反向传播。Song 等（NeurIPS 2020）给出精确实现；Millidge、Tschantz、Buckley（2020，arXiv 预印本）把结果推广到任意计算图（CNN、RNN、LSTM）。综述见 Lillicrap 等（2020，Nature Reviews Neuroscience）。证据：**强**（数学推导与实验）。
- **自由能框架。** Bogacz（2017，Journal of Mathematical Psychology）的教程逐步推导 Friston 的自由能框架，它扩展了 Rao & Ballard 的模型，学习由基于 Hebbian 的突触可塑性实现。证据：**中**（作为数学框架是严谨的；作为统一脑理论有争议）。
- **主动推断。** Friston 等（2017，Neural Computation）：所有神经处理与动作选择都可解释为最小化变分自由能，近似贝叶斯最优行为解释了寻求奖励、情境学习和认知觅食（epistemic foraging）。暗室问题由 Friston、Thornton、Clark（2012，Frontiers in Psychology）专门讨论。证据：**弱到中**。
- **世界模型。** Ha & Schmidhuber（2018）：VAE 把观测压缩为潜向量，MDN-RNN 预测未来潜状态，简单控制器可以完全在模型“想象”出的环境中训练。Dreamer（Hafner 等，2020）通过在学到的世界模型的紧凑状态空间里想象轨迹来学习长程行为。证据：**强**（多项基准上的结果）。
- **可验证原则。** Sutton（2001）《Verification, The Key to AI》：AI 系统只能在它能自己验证的范围内创造并维护知识。

### 前提是否成立

世界模型与 Dreamer 依赖可交互、可重置的环境和外部奖励；主动推断的完整形式需要显式的生成模型和偏好先验。柚子的环境不可重置，没有外部奖励；“偏好从哪里来”仍是讨论文档的开放问题。

### 与讨论文档的对照

- 一致：主动推断与“行动是对观测的补全”几乎同构；“agent 就是环境的模型”对应主从反转。
- 一致：世界模型在潜空间中想象与规划，支持“先压缩、再在压缩空间合成”。但 World Models 的 VAE 仍做像素重建；JEPA 是非生成的。两条路线在“是否需要重建原始输入”上分歧，这与问题五的结论相互牵制：生长需要某种原始层面的残差。
- 一致：Sutton 的可验证原则与讨论文档“世界是无法篡改的判定者”是同一主张。
- 提醒：自由能框架作为统一理论的证据最弱，讨论文档应把它当作形式参照，不当作依据。

## 我们自己的数据：旧试验品的回放实验

[长期记忆离线回放实验](memory-replay-evaluation.md)虽然测的是旧设计，但它是本项目唯一的一手数据。按本文的问题重读（以下各条的“含义”均为推论）：

| 观察 | 对新框架的含义 |
|---|---|
| 默认提示下模型会覆盖但不递归整理，摘要随阅读线性增长，prompt 达约 127k | 让主 LLM 在线维护结构不可行；支持把结构维护移出聊天循环（问题三的慢学习者） |
| 有可达路径但检索变成大范围枚举，12 次行动只有 3 次在正确路径上 | 支持一个便宜、非刻意的联想层（问题一的编码器） |
| 内联直接成员号使定向检索收敛，但形成成本约翻倍 | 显式索引有效但昂贵，支持把索引交给编码器而非 LLM 写出 |
| 一次“只判真假”的核验也会消耗数千 completion token | 用 LLM 调用逐条充当测量函数代价很高，支持小编码器 |
| 长思考经常以 `length` 截断，整理与聊天争夺同一上下文 | 与讨论文档“维护与行动争夺注意力”一致 |

## 横向结论

1. **框架的各个部件都有成熟的对应物。** 预测统一感知与学习（预测编码、自由能）、读即写（再巩固、先推断后可塑）、只预测表示（JEPA、CPC）、防坍缩（VICReg）、二阶惊奇（集成分歧、层级高斯滤波器）、行动即推断（主动推断）、知识即预测（PSR、Horde）、可达性的度量（反事实影响、empowerment）。
2. **讨论中可能的新意**集中在两处，都没有找到直接对应的已有工作：把二阶预测统一用于一切（包括学习过程），以及把“无常的可达性”作为唯一判据。没有找到既可能说明新，也可能说明有尚未暴露的问题。
3. **讨论中最大的遗漏**是互补学习系统与持续学习。引入可训练编码器后，灾难性遗忘是首要工程问题，而柚子完整保存的原始档案正好支持真实回放。
4. **一处内部张力**：讨论选择了“测量而不是生成”，但已有的结构生长方法都依赖相对于原始输入的残差。“几乎不压缩的原始测量”兜底因此是生长的前提，不是可选项。
5. **证据最强的**是 CLS 与睡眠回放、灾难性遗忘、稀疏编码、预测误差好奇的噪声失败；**证据最弱的**是自由能作为统一理论，以及预测编码作为实际皮层算法。
6. **一处更正**：VICReg 的第三项是视图间的不变性，不应直接写成“预测要准”。

## 对讨论文档的影响

本文不改写讨论文档的结论。建议在讨论文档中：

- 记录 VICReg 第三项的更正；
- 把互补学习系统与灾难性遗忘列为新的开放问题；
- 在第十节外部材料的预测编码表格处注明作者、年份已在本文核对。

## 参考文献

已核对标题、作者、年份、出处与摘要级要点，未通读原文。

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

**预测编码、主动推断与世界模型**

- Bogacz, R. (2017). [A tutorial on the free-energy framework for modelling perception and learning](https://doi.org/10.1016/j.jmp.2015.11.003). Journal of Mathematical Psychology.
- Friston, K. (2018). Does predictive coding have a future? Nature Neuroscience.
- Friston, K., FitzGerald, T., Rigoli, F., Schwartenbeck, P., & Pezzulo, G. (2017). [Active Inference: A Process Theory](https://doi.org/10.1162/NECO_a_00912). Neural Computation.
- Friston, K., Thornton, C., & Clark, A. (2012). [Free-energy minimization and the dark-room problem](https://doi.org/10.3389/fpsyg.2012.00130). Frontiers in Psychology.
- Ha, D., & Schmidhuber, J. (2018). [World Models](https://arxiv.org/abs/1803.10122). arXiv.
- Hafner, D. 等 (2020). [Dream to Control: Learning Behaviors by Latent Imagination](https://arxiv.org/abs/1912.01603). ICLR.
- Keller, G. B., & Mrsic-Flogel, T. D. (2018). Predictive Processing: A Canonical Cortical Computation. Neuron.
- Lillicrap, T. P., Santoro, A., Marris, L., Akerman, C. J., & Hinton, G. (2020). [Backpropagation and the brain](https://doi.org/10.1038/s41583-020-0277-3). Nature Reviews Neuroscience.
- Millidge, B., Tschantz, A., & Buckley, C. L. (2020). [Predictive Coding Approximates Backprop along Arbitrary Computation Graphs](https://arxiv.org/abs/2006.04182). arXiv 预印本（正式发表出处未核实）.
- Rao, R. P. N., & Ballard, D. H. (1999). Predictive coding in the visual cortex: a functional interpretation of some extra-classical receptive-field effects. Nature Neuroscience.
- Song, Y., Lukasiewicz, T., Xu, Z., & Bogacz, R. (2020). [Can the Brain Do Backpropagation? — Exact Implementation of Backpropagation in Predictive Coding Networks](https://papers.neurips.cc/paper_files/paper/2020/file/fec87a37cdeec1c6ecf8181c0aa2d3bf-Paper.pdf). NeurIPS.
- Sutton, R. S. (2001). Verification, The Key to AI. 个人网站短文.
- Whittington, J. C. R., & Bogacz, R. (2017). [An Approximation of the Error Backpropagation Algorithm in a Predictive Coding Network with Local Hebbian Synaptic Plasticity](https://pmc.ncbi.nlm.nih.gov/articles/PMC5467749/). Neural Computation.

**其它**

- Feynman, R. P., & Leighton, R. *What Do You Care What Other People Think?*，“It's as Simple as One, Two, Three”一章：费曼计数时在心里“说”，因而不能说话；Tukey 在心里“看”一条写着数字的带子，因而不能读书。
