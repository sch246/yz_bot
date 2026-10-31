# 中心 agent：经历日志、视图与上下文运行

- [Spec](spec.md)
- [Map](map.md)
- [验收记录](acceptance.md)
- [Decisions](decisions/)
- [Tickets](tickets/)

集成分支 `codex/chat-core-rewrite` 从 `79ec37b` 开始。各实施 worktree 只提交自己负责的代码；票据统一由本目录的 helper 状态管理，不使用各 worktree 中的票据副本。

```bash
python3 /root/.agents/skills/local-tickets/scripts/tickets.py --root /root/worktrees/bot-chat-core-rewrite/docs/working/tasks/chat-core-rewrite status --json
```

规格约束行为，票据保存认领与验证记录。最终交付须对整份规格验收；切片提交不能直接用于生产切换。
