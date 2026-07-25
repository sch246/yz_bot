# 未实现设计提案

本目录保存已经讨论、但尚未成为当前运行契约的设计。它们用于继续推演和拆分实施切片，不能用来断言 Bot 当前已经具备相应行为。

- [Core 与纵向失败域](core-and-failure-domains.md)
- [Storage 加载校验与历史恢复](storage-and-history.md)（热同步已实施，剩余部分仍是提案）
- [异常、日志与 link 报错节流](errors-and-logging.md)
- [权限演进](permissions.md)
- [多项目共存](multicore.md)

提案实现后，应把已验证的当前事实移入 `docs/architecture.md`、`docs/interaction-model.md` 或 `docs/runtime.md`，并删除这里已经完成或被放弃的迁移步骤；不要让提案与现行文档长期重复。
