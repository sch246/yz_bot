用现有 Python 按需检查自己的经历、记忆关系和聊天档案，不为临时问题增加专用工具。

## 三种记录

- `ctx` 只是“模块名 → 已加载模块”的字典，不是当前上下文。
- `ctx["oplog"]` 的结构化记录描述你实际读到、输出、行动和覆盖过什么。
- `ctx["chatlog"]` 保存聊天窗口实际发生的事，其中可能有你尚未正式读取的消息。
- 终端运行日志只用于排查代码如何执行，不是你的经历或聊天档案。

## 惰性检查经历

需要跨越较长时间检查自己的行为时，用现有 `exec_code` 调磁盘迭代器，不要把全部历史装成一个 list：

```python
records = ctx["oplog"].iter_events(start="20260925-1", stop="20260926-1")
selected = [row for row in records if row["kind"] == "output" and "关键词" in row.get("body", "")]
```

`start` 含、`stop` 不含；留空表示不限制这一端。返回的是普通 dict：

- `input`、`output`、`result`、`notification` 是有正式号的经历；
- `cover` 是独立的多父覆盖关系，`node` 指向总结行动，`members` 是它覆盖的正式号；
- 正式经历的 `references` 是正文或行动参数里实际出现的正式号，包括已经断掉的引用，便于自检。

迭代器逐条读磁盘，不改变 FIFO、水位、覆盖或运行中的上下文。Python 扫描过一条记录不等于你在聊天中重新经历了它；只有最终返回给你的筛选结果会成为本次行动返回。

若要把检查结果写成长期记忆，先让结果回到下一次模型请求，再用实际选中的正式号调用 `cover_events`。不要只保存变量名、查询代码或“当时符合条件的集合”，因为以后重跑可能得到不同成员。

聊天原话仍按窗口用 `read_messages` 查；未读 FIFO 仍用 `status`、`mentions`、`pull` 和 `mark_read` 管理。这个 Skill 只负责自由检查已经发生的经历，不替代改变事实的提交入口。
