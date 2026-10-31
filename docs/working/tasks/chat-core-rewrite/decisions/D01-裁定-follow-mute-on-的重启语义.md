---
{
  "id": "D01",
  "kind": "decision",
  "status": "resolved",
  "owner": null,
  "blocked_by": [],
  "invalidated_by": [],
  "title": "裁定 follow mute on 的重启语义",
  "type": "grilling"
}
---
## Sources / Parent

[规格](../spec.md#注意力与读位置)；[Claude 提案 §8](../../../proposals/chat-core-rewrite.md#8-需要维护者拍板)。

## 待裁定

loop、wait 已决定只放内存。follow、mute、短期 on 是否也在重启时丢弃？

推荐保留现有 follow／mute／on 持久化，先只迁移 loop／wait。另一选项是全部短期规则进内存。两者影响 awaker 文件、启动恢复和迁移删除范围。已向维护者发出异步问题，等待实际回答。

### 2026-10-31T09:44:21+00:00 · claim

Owner: linux:codex-root-chat-core

### 2026-10-31T09:45:27+00:00 · active → resolved

维护者实际回答：保留 follow／mute／on 的持久化，只让 loop／wait 在内存中。规格已按此记录；迁移保留这三种文件规则。
