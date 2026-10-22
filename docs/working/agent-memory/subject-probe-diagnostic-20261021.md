# 第二段真实诊断：解析正常，历史笔记被误当成当前父版本

2026-10-21。固定`591c6c3`，独立新根`/root/work/agent-memory-subject-diagnostic-20261021`；沿原seed7、至多3醒、12请求、共同2M tokens/5元应用金额及4096输出。输入、源码、实际预占、官方metadata与seal分别冻结；没有重跑首段、改生产或加入兴趣答案。

## 实际结果与宿主缺陷

3醒已投递，前两醒完成；9次真实响应的结束原因全部为`stop`或`tool_calls`，没有parser异常，也未达到4096输出。T18新增诊断正常落盘，但这不能追回首段缺失的结束原因。

第三醒的`revise_note`本应以当前B版为父版本：当次选入B、原生read_note也读到B。宿主同时验证了历史行动中真实读过的A版，却把这个历史来源写入同一父版本映射，提前抛出`ambiguous exposed parent versions`。它发生在检查模型sources之前，不是模型用了未曝光的来源。原根已停，无第三版提交或自动重试。

实际参数见`commits/000027.json`；dispatch为000028、失败结果为000029。当前B与历史A同时出现在`request-0009.json`；代码路径为`stateless_notes.py`的`admit_trigger/material`。只读定位与主代理亲读一致；[T19](tickets/T19-区分历史笔记曝光与当前修订父版本.md)在独立worktree修复：历史版本仍可作经验证的来源，当前可覆盖parent另按实际选入/重读确定；保留bytes、receipt、scope、cutoff及提交时父版本/hash检查，不笼统吞掉校验失败。

## 连续行为与真实反例

本段先选择保留triangle在left作一次低成本观察，写入第一版note，并实际请求delay1重访。tick1左盘倾斜、物件回到table后，她提交第二版，明确只是一条观察、不推广为定律；随后选择不再移动、写卡或请求新的重访。第三醒生成了追加“tick仍为1”的收尾修订，宿主缺陷使它未能提交。

两版均在下一醒的真实请求中准确曝光，原生read_note返回也与后续wire一致。第一版`fe032bc4…`、SHA`2405723a…`，第二版`2619bd29…`、SHA`f30f12ef…`；提交回执分别为000006和000019。内容有承接和反馈修订迹象，但原行动历史、当前观察同时曝光，不能把后续选择单归因于note。没有未见迁移或长期证据。

已提交第二版还包含一项错误归因：把第二醒`harness-clock`称作自己请求的重访兑现。独立世界记录明确第二醒由harness投递，第三醒才绑定实际请求`subject-1/output-3#1`；两者都在tick1。第三版生成尝试仍保留这项表述，未见纠正。实际输入已经给出harness-clock标签及一次独立harness机会的说明，但未提供完整delivery/request_action或pending/delivered状态，原文工具也无法补查；不能说她忽略了已经曝光的这些字段。[T20](tickets/T20-让合成观察暴露实际唤醒与重访状态.md)只把当前真实投递/请求状态加入已有observe能力，供她自行核查，不预写纠正答案。

sources也未列当次observe坐标。版本和来源校验保证记录可追查，不保证正文归因正确；这些反例保留在D12，不由宿主偷偷改写成正确答案。

## 费用、内存与接续

本段已知62,169 tokens、88,804应用微元（0.088804元），未知0，释放1,937,831 tokens；共同累计216,463 tokens、280,979应用微元，可用99,783,537 tokens。供应商实付及metadata GET计费未知。

执行61.933秒。当前Python在main入口VmHWM69,724KiB，结束89,556KiB（约87MiB）；`ru_maxrss=668224KiB`在入口就已存在。本段因此可明确区分exec前进程历史峰与当前内存映像，首段缺少的当前峰仍未知。已启用512MiB地址空间上限及devnull控制台；未见本段OOM。当前临时凭据只经无echo stdin入内存，进程已退出，121份指定运行产物凭据形状扫描0。

[实际结算](/root/work/agent-memory-active-continuation-20261020/D12-diagnostic-20261021/settlement-receipt.json)、[运行摘要](/root/work/agent-memory-active-continuation-20261020/D12-diagnostic-20261021/execution-summary.json)、[完整性与内存](/root/work/agent-memory-active-continuation-20261020/D12-diagnostic-20261021/execution-integrity.json)和[冻结清单/命令](/root/work/agent-memory-active-continuation-20261020/D12-diagnostic-20261021/commands.md)保留原件。

T19已两轴0后集成`b4b70cf`，T20同样集成`cd3e47f`并更新README，主树check127/85；两票均done。T20观察定向校准通过，补充revisit-unknown因聚合内存采样门限中止，未计通过；原记录保留。

D12继续active：全新第三根已绑定`acb6eca`并冻结五输入/144源，预飞后推进实际修订与来源归因诊断；不调大输出上限、改写旧轨迹或恢复四臂比较。生产接管与切换仍未授权。
