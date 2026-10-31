---
{
  "id": "T04",
  "kind": "implementation",
  "status": "ready",
  "owner": null,
  "blocked_by": [
    "T01",
    "T02",
    "T03"
  ],
  "invalidated_by": [],
  "title": "统一 run 与中心主循环接线"
}
---

## Sources / Parent

[规格](../spec.md)。基线 79ec37b。任务是同一集成分支上的原子切换；中间片段不承诺独立部署。

## Validation

亲自完整阅读修改文件与必要依赖，保护 WHY；不读生产数据，不启动真实 Bot，不新增永久测试。用仓库外合成脚本验证本票边界，跑 `uv run --frozen python run.py --check`。提交代码后记录 commit SHA、执行命令、实际输出和未完成接线。review 等待主代理审查集成，不自行标 done。

## What to build

在前三票合入后独占 mods/chat/agent.py、__init__.py、subcommands.py、observer.py，mods/tools/meta.py、agents.py，mods/llm/__init__.py、mods/bot.py；接管 roles.py 的 summarize 接线。可新增小型 run/opening/pressure helper，但不另造 manager 或镜像状态。把 main/handoff/reflect/observer/summary/fork 接到统一 run；current_wake ContextVar 隔离；完成 journal 和 read 确认边界；使用 attention 唯一调度与工具函数。opening 可编辑、两个截断显式提示；pressure 取代 subconscious。删除旧回调替换/口袋状态/loop timer、oplog 整套与全库消费引用、chat.cond/assign_tasks/旧开关。额外调用点变动先告知主代理协调所有权。

## Acceptance criteria

- [ ] 主轮仅一条执行路径，各类 run 有实际目的、计费/超时/取消/归档一致。
- [ ] 工具之前写输出、逐结果落盘，首次输出确认所展示消息，失败释放注意力认领。
- [ ] fork refs 和并发隔离成立，默认禁 send/type_word、显式 grant 可开且不污染主状态。
- [ ] opening、压力、读日志/搜索/交接/反思完整接线；原 WHY 行为保留。
- [ ] --check/--smoke 及合成 fake-model 集成场景通过；旧路径无运行消费者。

### 2026-10-31T09:45:28+00:00 · draft → ready

用户已明确要求子代理实施；规格、所有权与验收足以开工。

### 2026-10-31T09:49:12+00:00 · update

接线所有权补充：mods/tools/memory_system.py 的 read_context 改薄转调 roles；mods/tools/_vision.py 的 current_binding().session 改绑定 dict。LLM 必须新增逐调用开始/逐结果回调，journal.tool_started 在调用前落盘，segment.record_results 每结果后落盘；不能仍等整批结果结束才保存。journal/segment 将提供 raw_messages 与 begin_act/record_results(view,...) 包装。

### 2026-10-31T09:49:50+00:00 · update

基线确认：dismiss恢复阅读显示，不撤销已消费召唤。初始A已输出后dismiss不再醒；生成期间B未进有输出子请求则仍续醒；B进入late子请求且已有输出后dismiss不再醒，失败则仍续醒。对应agent752-789/screen329-368。T02实现确认消费，T04每次模型输出提交实际该请求patch并消费这些召唤，不能等整轮结束统一消费。

### 2026-10-31T09:52:33+00:00 · update

spec补明现行边界：dismiss重挂wait失败不撤回，已消费召唤不复活；实际非消息唤醒写me chatlog；todo/未读/时间/面包屑/observer/pressure只作当次hint；整轮fold首尾保护而当前轮完成步骤可收。T01/T02已失效并通知其核对重验，源接口不变。

### 2026-10-31T09:56:33+00:00 · update

root核查发现行为文档过时：基线meta._box_window WHY与send me分支记载10-27维护者已拆掉交接/反思不发言代码限制，保留type_word/send及me自语。新默认禁发言只针对fork；T03已收到修正kind过滤要求。T04不要给handoff/reflect重新封嘴；这两角色current_wake仍不能借主唤醒控制状态，但工具可用自己的binding结束当前run。T06同步修正文档过时措辞。
