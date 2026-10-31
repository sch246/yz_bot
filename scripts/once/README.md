# 聊天核心离线转换

`migrate_chat_core.py` 把 79ec37b 的 `data/context`、`data/event_stream` 和
`agent.seen` 转为 journal、view 和 `agent.read`。脚本只供这次转换使用；真实部署
完成后再退休，不是运行时兼容路径。相关约束见 [运行边界](../../docs/runtime.md) 和
[交互模型](../../docs/interaction-model.md)。

先停止旧 Bot，并使用完整的离线 runtime root。脚本不会停止进程；文件锁只防止同时
运行两个转换器。显式路径中应包含旧 `data/` 与 `chatlog/`。不要在运行中的库上应用。

```sh
uv run --frozen python scripts/once/migrate_chat_core.py --runtime-root /absolute/offline-root
uv run --frozen python scripts/once/migrate_chat_core.py --runtime-root /absolute/offline-root --apply
```

第一条默认 dry-run，在临时目录重建、校验并演练下一次操作，源目录不写入。
只有 `--apply` 才创建锁、备份和迁移记录。退出码 0 表示校验通过、应用完成或已经迁移；
2 表示存在必须处理的阻断；1 表示其它失败。JSON 报告只含状态、固定原因、数量和路径，
不打印消息、工具参数或异常正文。不要把 runtime 内备份或 manifest 当作公开报告上传。

转换保留正式行动编号、原生 assistant/tool JSON、当前笔记来源和版本、唤醒编号及
草稿/read 快照、画面记录身份、当前折叠树和步骤地址。旧 oplog 未保存的 thinking 仅从
能唯一匹配的上下文补回；旧 compress 入参截短仅用于核对投影，journal 保存完整原文。
无结果的调用保持未知，不写成成功或自动重跑。全局 `active_tools` 和其它 agent 设置保留；
schema 顺序进入 view 的 `tool_state.schemas`。`follow/mute/on` 原文保留，旧 `wait`
行删除，`loop/subconscious` 全局运行开关清除。`boxes.json` 等其它文件原地保留。

冻结历史中不可分解的消息以明确 `legacy` 来源保存，旧上下文元数据保存在
view.stats.legacy_metadata，旧折叠名称保存在 legacy row 的 legacy_fold_sources。
当前折叠引用则恢复实际 covers。脚本不会为缺失的旧快照猜一个唤醒、行动或工具状态。

以下情况会阻止应用，并给出对应来源位置：

- 当前消息没有完整唤醒快照，或第一份快照前还有无法归属的消息。
- 当前行动找不到唯一 oplog 原文，工具结果无法配对，或正式编号与关联分片日冲突。
- 当前画面头无法唯一对应原始 chatlog、唤醒前画面边界缺失，或末尾位置不符。
- 当前笔记来源/版本不符，工具宣告的状态快照无法恢复，包括展开当前折叠后需要的状态。
- 当前折叠源缺失、循环，旧 oplog 有坏行或未完成尾部，源在转换期间变化，或存在符号链接。

`data/pyload.py` 和 `data/storage/links.json` 中的旧公开名只报告文件和行号，不执行、
不替换。调用方应依据最终 API 自行处理这些位置，脚本没有 `--force` 丢弃路径。

应用会先复制并逐文件校验将修改的 owner，再校验准备目录，最后切换。旧 owner 移入
`data/legacy/chat-core-v1/data/`；备份、准备目录和写前记录位于
`data/migrations/chat-core-v1/`。成功标记为 manifest.json 的 `status: complete`。
只含 oplog 的旧库也会创建空 `data/chat/view.json`。完成后再执行会返回
`already-migrated`，不会重复添加条目。日常运行后 journal 继续增长不影响这一判断。

切换异常会尝试按写前记录倒序恢复。进程被强制终止后，先保持 Bot 停止，再执行：

```sh
uv run --frozen python scripts/once/migrate_chat_core.py --runtime-root /absolute/offline-root --apply --rollback
```

恢复保留备份和新准备目录，不删除冲突文件；路径状态冲突时停止并报告位置。
`rolled-back` 后可以重新 dry-run/apply；新一次 apply 将旧尝试留在同级
`chat-core-v1-rolled-back-*` 目录。已完成的迁移不通过这个恢复命令撤销。
