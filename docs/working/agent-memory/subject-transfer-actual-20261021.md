# 旧经验进入新合成处境：实际结果

2026-10-21，固定集成提交`7605f84`。T21来源绑定返工及两轴审查通过后，在全新根`/root/work/agent-memory-subject-transfer-20261021`执行既定seed8片段。3醒、10次真实请求正常结束并结算，原根永久关闭，不为得到期待行为再跑一段。

本次确有超过复述的局部行动承接：旧笔记中未执行的试验进入新重访理由，模型观察新状态、改变布局，收到不同结果后写卡。但delivery归因和跨运行推断仍有明确错误；正常运行完成不等于D12通过。

## 输入与实际行动

6输入、144源码冻结；既有合法读取核验第三段`global/main` v2，版本`e74848c552b00c1251043c589ab573a3a19ec63b3d7a082dbbe37662f2470390`、SHA`0d5884edfb38254145971f826ce573d3c78ab7a243761aeb287ca9f1cbba2343`。10份实际wire均保留原bytes，不带旧行动journal；后续选入的是本次已提交行动。旧根149文件hash不变。模型得到literal note及“只读历史、不构成当前观察”的说明；确切旧workspace/seed身份只在控制面，不能声称已送入模型。没有note库、writer或读写note工具。

以下commit序号均指本次根的`commits/`，完整内容和字段坐标见[独立轨迹复核](/root/work/agent-memory-active-continuation-20261020/D12-transfer-20261021/trajectory-review.md)。

| 阶段 | 实际证据 | 可以支持的判断 |
|---|---|---|
| 第一醒，bootstrap/tick0 | `000003`观察circle在table、triangle在left；`000004/000006`将circle移至right；`000007/000009`请求1tick重访，理由明确接续旧note未执行试验。`000010`把布局称为旧试验的镜像 | 旧内容进入具体理由，实际行动与旧布局不同；不是把旧关闭状态直接当当前事实。移动先于解释，不能只凭事后理由证明内部因果 |
| 第二醒，harness-clock/tick1 | `000014`右盘倾斜、circle回table、triangle留left；`000015/000017`写卡因超过512 UTF8 bytes失败，`000018/000020`缩短后成功。卡文分开本次观察与旧历史，把circle-tray/交替/随机列为未测候选 | 当前反馈进入后续请求及实际外部记录；工具失败后调整参数。是新工具调用，不是HTTP重试；卡片不是持久note修订 |
| 第三醒，model-revisit/tick1 | `000025`实际绑定`subject-1/output-3#1`且delivered，时钟和last_clock不变；`000026`明确没有第二次本run试验，只观察后结束，无新重访或世界修改 | 能在这一醒识别同tick重访没有新试验。三醒最终回复都非空，不记为实际静默 |

## 反例及边界

第二醒的权威观察已经给出`harness-clock / request_action=null / revisit=pending`，`000021`仍说自己的请求交付了这个clock。第三醒后来正确辨认实际model-revisit，不能抹去先前误归因。

旧note原文是“looks fixed by the mechanism/seed”，并明确单次试验不能预测下一次，另列fixed/alternating待测。模型把它收窄成fixed-left，再以新右盘结果宣称推翻。新结果只反对跨运行一律左盘的说法，没有推翻“由机制/seed决定”；seed7→8又是混杂，不能称已控制object identity或识别倾斜机制。卡文中两份材料的circle同盘现象可记录，不能升级为circle导致倾斜。

第三醒又以旧note的布局、卡片与本次不同称“直接矛盾，或是另一运行，或是错了”。不同运行的事实差异不证明旧历史错误；第一、二醒其实已把它称为旧运行。这里保留来源身份分辨不稳定的反例，不把它写成成功纠错。第三醒把跨运行候选保留为未经验证是假说的收束，但不能补足这些错误。

此次只改变同类有限世界的seed，没有无note对照；后醒还看见本次行动、当前观察和卡文。支持旧经验局部参与理由及反馈后的变化，不证明note的独立因果效果、广泛迁移、长期持续主体或科学推断可靠。原生可逆工具没有提供，不能将没有回退解释为模型拒绝纠正。

## 结算、资源和原件

10请求全known，实际响应7个tool_calls、3个stop，无parser异常、unknown或HTTP重试；7个工具结果中6个completed、1个failed。75453 tokens、130092应用微元（0.130092元），释放1924547 tokens。共同累计376021 tokens、532422应用微元（0.532422元）、59生成行，可用99623979 tokens，当前预占和未知均0。5元/2M只是本段技术上限；应用折算不是供应商实付，metadata GET及一般失败计费继续未知。

总时102.574秒；512MiB地址空间上限，当前Python入口/结束VmHWM69868/89648KiB。ru_maxrss668224KiB入口已存在，不当作本Python峰。metadata GET成功，目标容量1048576/393216。指定131份产物凭据形状扫描0命中，只证明该检查范围；凭据只经无echo stdin，进程exit0，无新凭据文件/relay。

[执行摘要](/root/work/agent-memory-active-continuation-20261020/D12-transfer-20261021/execution-summary.json)、[结算回执](/root/work/agent-memory-active-continuation-20261020/D12-transfer-20261021/settlement-receipt.json)、[独立完整性审计](/root/work/agent-memory-active-continuation-20261020/D12-transfer-20261021/execution-integrity.json)、[关闭运行单](/root/work/agent-memory-active-continuation-20261020/D12-transfer-20261021/commands.md)、[先前计划](subject-transfer-run-plan-20261021.md)。T21初次审查失败、返工和原预飞均保留。

本轮到此收束为[D12阶段答案](subject-experience-stage-answer-20261021.md)。继续推进T22单中心双窗口隔离宿主，仅Mock/假出口；不读取生产、迁移材料、发送QQ或重启。
