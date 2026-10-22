# Cortico 的实时事件与连续行动

2026-10-22 定向源码核验，回答“实时反应和连续移动怎样实现”。固定公开版本 `02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7`（0.2.6）；未运行 Cortico、游戏或模型，不据此报告实际延迟。

## 连续移动由 Minecraft World 执行

- `mc_do` 提交技能序列后立即返回受理回执，后台执行器逐步运行，完成、受阻和抢占另以 `minecraft.task` 事件报告。任务运行不要求模型一直生成。队列的 `replace` 保留当前任务、替换后续任务；`append` 追加；`now` 请求中断当前任务并插队，自保任务有例外。
- 寻路通过 Mineflayer/pathfinder 持续控制身体。上层给目标，底层推进路线；并非每移动一步都调用模型。
- 防烧和防溺水反射启用时按 200ms 心跳检查，直接控制自保并事后汇报，不经过模型。战斗、环境自保与任务分别协调身体控制权。

来源：[执行器契约](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/worlds/minecraft/executor.ts#L1)、[后台启动](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/worlds/minecraft/executor.ts#L1923)、[寻路](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/worlds/minecraft/travel.ts#L449)、[反射心跳](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/worlds/minecraft/executor.ts#L3207)。

## 核心事件在请求之间接入，必要时取消当前轮

- World 决定事件触发模式。普通外部事件默认 debounce，可合批；piggyback 随其他批次进入，不单独唤醒。不是每个游戏物理刻都请求模型。
- `preempt` 取消尚未外化的模型轮；`interrupt` 可以打断已经外化的模型轮或向可中断工具发信号。新事件接入上下文后重新请求，不在线改写正在进行的同一次模型推理。
- 工具须声明可中断并合作响应 signal；核心等待 handler 返回，不强杀任意工具或保证外部动作即时停止。取消推理和停止已排队游戏任务属于不同控制路径。
- 流式响应中的完整工具调用可以提前派发，降低首个动作等待；实际延迟仍受事件策略、模型和执行器影响。

来源：[触发契约](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/core/types.ts#L599)、[取消当前轮](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/core/loop.ts#L421)、[提前派发与重新请求](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/core/loop.ts#L1164)、[工具合作取消](https://github.com/Pal-AI-Lab/Cortico/blob/02682f69065dfaa4d9ff9a4b25a8f8e67ba006e7/src/core/loop.ts#L2103)。

## 对当前研究的含义

以下是工程推断，不是 Cortico 的实测：前置选材、主体决策和后置整理可以与已下达动作的持续执行分开。是否采用这种接线，要明确任务生命周期、停止入口、回执与新决策的关系；统一事件流本身不会生成连续运动能力。本文仅补证，不改变既有票据状态或授权。
