用现有 Python 按需检查自己的经历和聊天档案，不为临时问题增加专用工具。

`ctx` 是“模块名 → 已加载模块”的字典。`ctx["chat"].journal` 保存不可变经历，`ctx["chatlog"]` 保存窗口实际发生的原话。上下文是这些经历的编号视图，归档在 `data/chat/views/`；加载 memory_system 后用 `read_context` 读。

检查当前材料占用时，用 `read_context("current", detail="outline", request="first")` 看本段第一次实际请求，`request="latest"` 看最近一次。current 跟随当前工具所属运行，构造和分身各看自己的 ctx。概览列出系统头、材料引用与来源、附图、末尾 hints、工具声明的字符数和文本估算，以及该次请求供应商报告的 usage。构成是供应商转换前口径，文本估算不含真实图片 token；cached 未报告显示 null，不能当成零命中。概览不包含材料正文，正文仍用默认 detail="body" 和原有分页、match。旧归档没有记录时会明确说明。

`segment_usage` 累计本段所有请求，不只 first 和 latest：`known` 是已报告字段的小计，`coverage` 是各字段报告次数，和本段请求数一起读。覆盖不足时，小计不能当作完整用量；`cached_all_reported` 说明缓存字段是否覆盖所有请求。

需要按时间检查行为时，可用现有 exec_code：

```python
rows = ctx["chat"].journal.events(kind="assistant", after=开始的Unix秒)
selected = [row for row in rows if "关键词" in str(row.get("messages", []))]
```

`events` 返回普通字典列表，按日志号顺序，after 包含边界；kind 不填时包含所有类型。assistant 是模型输出，tool 是单项结果，tool_started 是派发记录，act_end 表示该批结束。缺结果的已派发项仍可辨认，查询不会重跑动作，也不会改变阅读状态。长历史先限定日期、ctx 或 kind，再筛选需要的内容，避免一次返回全部原文。

只有最终返回给你的筛选结果会成为当前行动结果。要留下结论，就写明结论与依据（日志号或聊天窗口和时间），当场或沉淀时再决定写进哪份笔记。聊天原话用 read_log，跨记录、笔记、日记和上下文找线索用 search。
