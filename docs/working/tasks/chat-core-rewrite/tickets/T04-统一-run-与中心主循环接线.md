---
{
  "id": "T04",
  "kind": "implementation",
  "status": "done",
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

### 2026-10-31T10:16:33+00:00 · claim

Owner: bot:implement_run

### 2026-10-31T10:23:30+00:00 · update

Parent authorized segment.py small API ownership: extract pure validate_dismiss reused by dismiss; main tool validates before wait and revalidates at final drop. No spec change.

### 2026-10-31T10:39:52+00:00 · active → review

交付 HEAD d24d080（分支 codex/chat-core-run，worktree /root/worktrees/bot-chat-core-run）。前置基线 a0d3695；本票提交 c580c9a（纯validate_dismiss接口）、1d3ca4c（逐工具LLM回调，worker原4afcf350）、ff940e3（observer/summary，worker原3f5d5ac）、1ab1d62（fork/vision/memory，worker原62b3e97）、d24d080（主接线与删除）。所有子提交已审diff再cherry-pick，无生产runtime读取/真实Bot/QQ/永久测试。

统一API：chat.run(items,goal,*,tools=(),model=None,kind="fork",view=None,stop=None,timeout=None,state=None,prepare=None,hints=(),on_output=None,after_round=None,effort=None,grant=())->(ctx,text)。items保持混合refs/message顺序，goal非空、journal metadata保存、请求末尾hint提供；read_context可读目的而raw_messages不变。无view的独立run persist=False，finally归档原refs/输入/部分结果，不覆盖主view；main/handoff共享主view。binding普通dict的run承载view/kind/persist/end/action/cancelled等运行态；current_wake ContextVar只在main及其工具有值；辅助角色显式隔离。统一Chat构造、冻结工具快照、计费、超时/取消、output/start/每结果落盘及act结束。write(state,operation,...)只将OSError记为本run failure并停止后续派发，参数ValueError仍可处理，下一独立run不熔断。observer保留stream=False与内部输出不混主自语。

主reader：attention唯一计时、callback只通知；active普通dict+同锁登记/注销/到达接力。record_event只register(kind="")，命令/link之后capture才on_record；登记失败仍可明确维护命令。首次及后续成功输出只提交实际request_patches并ack相应召唤；失败release不热重试。事件/时钟均留me记录，循环/wait保留理由、秒级由attention管理；^C按关联窗口停主watchdog，私人^C不产生新召唤。dismiss先validate_dismiss、plant wait成功再申请、批结束重验再drop+restore_read；重验阻拦会持久说明已有wait保留。global active与view schema分属两权威；fork工具/schema/grant独立，默认排除send/type_word，不可load偷回；明确授权可用。fork(goal,refs="",tasks="",tools="",model="")最多5路，失败也返回ctx。

opening唯一policy通路：data/notes/policy/opening无扩展，逐行refs；默认global/recent+前6篇日记+今天、years、目录；空文件可显式无默认refs，坏引用可定位；人物20/目录60遗漏数及召回入口明示；已载笔记与当天/跨天刷新。pressure统一tidy.py、两醒宽限、5万增长提示/较重已完成step/2h recent提示、连续失败2次或2倍长度强制归档、最多3次handoff。handoff异常/取消/最后仍tool_calls不归档；forced警告写新view。reflect keep不碰主view；summary/observer均无工具由run显式集合控制。

删除旧职责/迁移公开名清单：整个mods/oplog包（含全局熔断/事件mirror/replay）；context.WindowTurn及begin_turn/get_turn/end_turn/finish_turn/cancel_turn；chat.init_chat/_activate_chat/_restore_agent_tools/get_handler/cond/mark_window_read/_schema_modules/_persist_schema_modules；旧agent循环timer/loop.jsonl/clear_loop_history；tools.agents.assign_tasks改fork；meta.clear_loop_note；#loop/#subconscious总开关；vision动态session队列/provider。debounce.py已由T02删除，本票清掉消费者。旧runtime仍可能引用这些公开名，迁移脚本只定位报告，由维护者处理；无运行fallback。启动guard只检查旧data/context或data/event_stream存在、新view与迁移manifest状态，提示scripts/once/migrate_chat_core.py --runtime-root ABS，未完成提示rollback；新装正常。

实际验证：变更前 uv run --frozen python run.py --check=>146/88，--smoke=>86/88仅mcf/minecraft；最终--check=>146 Python/87 public modules，git diff --check通过；isolated --smoke=>85/87仅mcf/minecraft（删除oplog导致总数减1，无新增失败）。uv run --frozen python /tmp/check_llm_run_callbacks.py ALL9CHECKSPASSED（worker及root已实跑）；uv run --frozen python /tmp/check_chat_role_runs.py ALL7CHECKSPASSED（worker/root实跑）；python3 /tmp/check_chat_run_tools.py 全通过（fork5并发/独立grant&state/ctx失败、vision/memory边界）。uv run --frozen python /tmp/check_t04_run.py 全通过：真实统一run多角色/goal/计费/独立归档，late B未入请求继续醒与已输出确认不复活两路，失败释放+已保存画面重试仅一次，dismiss wait失败保留view/read，wait成功后同批新草稿重验拦截仍保留wait，首工具取消仍存首结果不派发第二工具，一次OSError本run停且独立run恢复，无效compress参数不误停，image一次投影不入journal，refs/message/refs顺序+readable目的，实际_work白醒、handoff失败/成功/forced、reflect keep。uv run --frozen python /tmp/check_t04_edges.py 全通过：真实bot._route command/#/link/private/self/^C/registration失败仍命令，push合法范围，opening默认/custom/空/坏ref/refresh，两截断，fresh/历史only/未完成/完成migration guard，reader失败不spin+晚到无缝接力。所有脚本在/tmp合成runtime、fake模型/接口，无真实发送。

剩余风险：尚未用真实provider/NapCat或真实数据部署验证；本票交主代理Standards/Spec审查，离线迁移脚本T05仍由其owner交付。

### 2026-10-31T10:41:18+00:00 · claim

Owner: linux:codex-root-chat-core

### 2026-10-31T10:41:25+00:00 · review → ready

主代理实跑run与edges脚本均过，但Spec发现今日无日记时只挂6篇，与最近7篇合同不符（edges也镜像assert6需改）。Standards独立复现timeout：assemble持guard调用prepare阻塞，超时except/finally再等同guard，timeout=.05+prepare Event.wait(3)实为3.01秒返回；无限等待会卡死。移出耗时prepare，超时关闭不等worker锁，并防迟到写已归档view。修后提供定向证据，独立Spec其余审查仍在进行。

### 2026-10-31T10:43:20+00:00 · claim

Owner: local:implement_run

### 2026-10-31T10:45:32+00:00 · update

审查返工新增已证实边界：默认opening须最近7篇存在非空且非未来日记；超时不可反等worker持有的guard/fsync锁，迟到线程退出后再归档并显式finalization pending；运行中到达reflect控制job不得被late屏幕/首次输出ack吞掉，须保留至后续控制分派。正在补对应合成证据。

### 2026-10-31T10:48:05+00:00 · active → review

返工commit 6c49c36（完整SHA见git；若短SHA由提交输出不同则以下实际git结果为准）：默认最近7篇存在非空且非未来日记，今天晚创建仍refresh；late reflect claim立即release且不进入main采用/ack，成功tool后续请求与当前请求失败路径均最终控制分派一次；prepare锁外，超时caller先closed不反等guard或segment/fsync锁，worker退出后后台归档，state.finalization显式pending/complete/failed。超时返回不代表已冻结，永久不退出worker将保持pending；普通成功/异常仍同步收尾。实际验证：uv run --frozen python /tmp/check_t04_edges.py PASS（today无/有/晚创建+空/未来排除）；uv run --frozen python /tmp/check_t04_review.py PASS 5组（prepare/fsync受控Event在release前caller已TimeoutError，禁注入也拦late sources，release后冻view稳定/main不覆写；真实watchdog prepare阻塞；late reflect成功与失败各一次）；uv run --frozen python run.py --check 146 Python/87 modules；--smoke 85/87仅baseline mcf,minecraft；git diff --check PASS。只改agent.py/run.py，无永久测试/生产数据。

### 2026-10-31T10:48:12+00:00 · update

提交号更正：本次返工真实完整SHA为94f8bb00c5860baa4bcfb37072b06ae3cc1bd514；上一条日志的6c49c36为录入错误，不存在该交付。验证结果与文件范围不变。

### 2026-10-31T10:51:28+00:00 · claim

Owner: codex:root-final-review

### 2026-10-31T10:51:36+00:00 · review → done

主代理审查全部交付 diff 并集成 c580c9a..94f8bb0；最终快照 e17a586。独立 Standards/Spec 定向复核三项收尾问题通过；主代理复跑 run、edges、LLM callbacks 9 组、辅助角色 7 组及 timeout/late reflection 5 组通过。超时先返回，后台未结束时 finalization=pending，完成后归档；此边界写入文档。整体 smoke 与正式迁移 CLI 由 T06 最后验收。
