# 第三段真实诊断：修订完成，正确区分两种唤醒

2026-10-21。固定`acb6eca`，全新根`/root/work/agent-memory-subject-parent-fixed-20261021`；五输入/144源重新冻结，沿seed7、3醒/12请求/2M tokens/5元应用金额/4096输出。T19分开历史来源与修订parent，T20仅补实际delivery/revisit观察；两项均经独立两轴及主代理审查集成，未预写兴趣或纠正答案。

## 实际行为

模型先观察、读取不存在的note基线，自己把circle移到left、triangle移到right，请求delay1重访，说明想看哪个有物件的盘会倾斜。未提前声称结果。第二醒的实际来源是harness-clock：left倾斜、circle滑回table，旧请求仍pending。她保存第一版观察，区分事实与尚未验证的推断，并未请求更多重访。

第三醒实际兑现第一醒的旧请求；同为tick1，没有第二次时钟推进。她同时observe/read_note，明确这只是重观察，不能算第二次试验；把第二醒补标harness clock、第三醒标model-revisit，并提交第二版。末次回复准确引用已提交的新版本、保留未做的后续试验，选择不再行动。

具体坐标：第一醒行动000007/000012、回执000009/000011/000014；第二醒observe000019，第一版生成/提交000023/000025；第三醒observe/read000030/000032，第二版生成/提交000033/000035，最后回复000036。第三醒投递绑定`subject-1/output-4#1`。这段没有重现前段的harness归因错误，但不是受控比较，不能把差异全归因于新增字段。

## 笔记消费、来源与限制

第一版`85b7715091b4a039f892acab69d7d24b74e746d5e85bb21d44f53b57ea79c577`，SHA`45c75b35b5a68805ba3477753a44238d351b9b9ff96d99b463a59800f19bdb7a`，在第三醒真实请求及原生读取中准确曝光。第二版`e74848c552b00c1251043c589ab573a3a19ec63b3d7a082dbbe37662f2470390`，SHA`0d5884edfb38254145971f826ce573d3c78ab7a243761aeb287ca9f1cbba2343`，确认提交及回执可见；没有后续全文读回或跨醒消费。

真实修订不再被历史基线/当前note的parent竞争阻断。第一版可见、后续内容承接及新证据后的修订已有局部证据；行动历史和当前观察也同时存在，仍不能证明note独立贡献。两版sources遗漏部分实际观察/移动来源，正文可交叉核验不代表来源坐标完整。

另保留理由不严谨之处：最后称没有时钟推进时重排就不可观察，实际上移动本身有即时回执。此处不影响已记录的世界状态，却不应当作正确一般原则。未见迁移、长期保留和回退后的真实再消费未验证；运行complete不等于D12完成或主体目标达成。

## 结算与资源

3醒完成、12响应均settled：9个tool_calls、3个stop，无parser/host异常，closed为finite-subject-segment-ended。已知81,105 tokens、113,353应用微元（0.113353元），未知0，释放1,918,895 tokens。共同累计297,568 tokens、394,332应用微元、48生成请求行；可用99,702,432 tokens。供应商实付及metadata GET计费仍未知。

执行98.788秒；当前Python入口/结束VmHWM69,772/89,652KiB。ru_maxrss668224在入口已有，不能充作当前映像峰。512MiB地址空间上限与devnull保留；临时凭据只经无echo stdin，进程已exit0，指定产物凭据形状扫描0，未落key文件。两旧根及本根均不重跑。

[完整性/算术复核](/root/work/agent-memory-active-continuation-20261020/D12-parent-fixed-20261021/execution-integrity.json)、[独立轨迹复核](/root/work/agent-memory-active-continuation-20261020/D12-parent-fixed-20261021/trajectory-review.md)、[实际结算](/root/work/agent-memory-active-continuation-20261020/D12-parent-fixed-20261021/settlement-receipt.json)、[执行摘要](/root/work/agent-memory-active-continuation-20261020/D12-parent-fixed-20261021/execution-summary.json)、[冻结清单与命令](/root/work/agent-memory-active-continuation-20261020/D12-parent-fixed-20261021/commands.md)保留原件。

D12继续active。下一项先核现有接口能否在全新隔离Chat中准确读取第二版、去掉旧行动历史，缩小消费归因疑点；未获得支持的来源/parent边界不放宽。生产、QQ、真实材料、重启或迁移授权不扩大。
