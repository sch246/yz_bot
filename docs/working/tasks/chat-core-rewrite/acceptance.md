# 中心 agent 重构验收

实施基线：`79ec37b`。集成分支：`codex/chat-core-rewrite`。对应[规格](spec.md)及[票据](README.md)。

代码验收快照：`e17a586`。所有实施切片已集成，最终编译、隔离启动、正式迁移 CLI 均通过。行为文档提交为 `168c495`。

## 验证范围

验证均使用仓库外脚本、临时 runtime、合成消息和假模型，没有启动真实 Bot、发送 QQ 消息或转换生产数据。没有增加永久测试套件。

| 规格范围 | 已执行证据 |
| --- | --- |
| 日志、编号、视图和前缀 | T01 合成脚本验证 80 线程及 60 进程追加、原始消息、部分结果、原子视图和备份恢复。主代理复跑尾行恢复与归档中断证明。 |
| 折叠、展开、撤回 | 稳定整轮/步骤地址、嵌套展开、首尾保护、发言/草稿/follow 三种拦截、撤回后阅读恢复通过；原消息与当前轮 reasoning 保留。 |
| 行动持久化 | LLM 回调 9 组检查通过：输出/开始先于派发，结果逐个保存，取消保留第一项结果，写失败停止后续工具。回拨 5 秒及跨日冷读仍关联结果、拒绝重派。 |
| 阅读与唤醒 | attention 15 组检查通过，覆盖同秒后到、旧时间点名、普通离线补回、生成期间新消息、失败释放、dismiss 消费边界。普通补回显示与未读判定分开。 |
| 调度与持久规则 | 默认触发、mute/master、follow、on/wait、防抖、秒级 wait、loop 停止、回调取消替换和单计时线程通过。follow/mute/on 持久，loop/wait 进程内。 |
| 统一运行与分身 | 主代理复跑 run 与路由脚本：独立角色归档/计费、工具绑定、late 消息、局部写失败与恢复、图片单次投影、混合引用顺序、交接成功/失败/强制归档。工具切片验证默认发言过滤、显式授权、线程隔离、reload 和 last-good。最终 5 组证明覆盖阻塞 prepare、阻塞 fsync、真实 watchdog、晚到反思与主请求成功/失败；超时及时返回，迟到材料不能污染新主视图，反思不会被普通消息确认吞掉。 |
| 开局与可见遗漏 | 自定义 opening、空策略、坏引用、笔记刷新、目录 60 份和人物 20 份遗漏提示通过。最近七篇非空、非未来日记及当天日记晚创建后的追加通过。 |
| 工具与辅助角色 | 主代理复跑工具目录的两份 58 项验证，以及旁观/汇总的 7 组检查；工具激活、schema、权限、空闲回收及辅助角色原提示保留。 |
| 离线转换 | 主代理复跑 18 组转换/故障场景及 2 个行动来源边界检查。包含原 JSON、来源元数据、fold/dismiss 后续行为、旧时间召唤、首 manifest 失败、真实子进程退出、回滚及重试。 |
| 正式迁移命令 | 最终集成代码使用普通模块导入，在临时 runtime 执行 CLI：dry-run 源文件逐字节不变，apply 成功，重复 apply 与应用后 dry-run 返回 already-migrated。没有使用 namespace shim。 |

这些场景验证明确的工程合同；它们不证明长期记忆已能可靠形成洞察、自动确定正确目标，或找到最佳上下文组合。

## 检查入口

切片脚本与故障证明保存在本次会话的 `/tmp`，不是项目长期接口：

- `/tmp/check_t01.py`、`t01_recovery_proofs.py`、`t01_clock_proofs.py`、`t01_note_sources_proofs.py`。
- `/tmp/verify_chat_attention.py`、`t03_validate.py`、`t03_validate_module_import.py`。
- `/tmp/check_llm_run_callbacks.py`、`check_chat_role_runs.py`、`check_t04_run.py`、`check_t04_edges.py`、`check_t04_review.py`。
- `/tmp/check_chat_core_migration.py`、`check_chat_core_owner.py`、`check_chat_core_cli.py`。

clock 和 note_sources 脚本以 `python -c "from pathlib import Path; exec(Path('/tmp/脚本名.py').read_text())"` 执行。早期迁移合成脚本用 namespace shell 跳过尚未接线的 chat 入口；正式 CLI 已另行验证正常导入。部分脚本固定引用实施 worktree，因此不能作为脱离本次会话的通用命令。

## 最终交付检查

审查基线为 `79ec37b`。各切片经过 Spec 与 Standards 审查后合入；整体验收按上表覆盖跨切片调用、已移除职责、重启语义和失败恢复。主代理核对最终文档 diff，旧研究提案保留历史原文并指向当前规格。

基线为 check 146 Python files / 88 public modules，smoke 86/88，optional failures 为 mcf、minecraft。最终集成结果为 check 146/87、smoke 85/87，仍只有这两项 optional failures；公共模块数减少来自删除 oplog。最终 smoke 输出保存在 `/tmp/bot-chat-core-final-smoke.log`。

源码旧路径检查只剩迁移门禁、历史说明及 attention 中仍保留的 debounce 行为名；没有旧 oplog、WindowTurn 或 subconscious 执行路径。

原 `/root/bot` 的 22 份已有修改及未跟踪源码与工作开始时 SHA-256 一致。文档相对链接与最终 `git diff --check` 通过。

运行超时会先返回；若模型或文件操作仍阻塞，`finalization=pending`，等工作线程退出后再归档。永不返回的工作线程不会自动完成归档。

真实停机、生产数据转换和启动尚未执行。当前交付为可审查代码、转换工具及验证证据。

实施 worktree 暂保留：本次 `/tmp` 验证脚本仍引用其中路径，可用于复核交付；它们未连接真实运行数据。
