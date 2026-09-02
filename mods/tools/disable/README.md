# 未启用的历史工具

本目录只保存一份决策记录：历史源码中曾实现、但 `add_tool` 注册被明确注释掉的工具，
为什么至今没有成为 `mods/tools/` 的正式模块。这里不再保存它们的 `.py` —— 一份永不
被 registry 扫描、也永不运行的实现，只会成为需要跟着改的死重量。需要旧代码时从 git
历史取回（整理版本见 `8c4385e`，删除见其后的清理提交）。

| 历史工具 | 当时的语义 | 现在的位置 |
|---|---|---|
| `read_data` | 动态环境的 `data` | `storage.get("", "py_data")` 唯一持有，`.py` 里直接可读 |
| `group_size`, `group_members` | 当前群实时成员列表 | `identity.memberlist`，`.py` 里直接可读 |
| `lunar_date`, `xiaoliu` | 历法与小六壬 | `mods.lunar`，并有 `.jrlp` 等命令 |
| `sendmsg` | 向当前窗口或显式目标发送 | `message.sendmsg`；旧说明称 `user_id`/`group_id` 冲突却会一起下传，是真 bug |
| `later_list`, `later_set` | 列出与修改延时任务 | `.later` 命令；旧 `later_set` 签名里的 `code` 从未进入实际命令 |
| `url2cq` | URL 转图片 CQ | `mods.image` 的统一内容寻址缓存 |
| `baidu_encyclopedia` | 第三方百科接口 | 依赖 `api.wer.plus`，非官方 API，可用性不明 |

三个只剩名字、没有可恢复实现的项：

- `rag_search` 只有整体被注释的草稿，它引用的 HippoRAG 模块会在导入时初始化 LLM、
  embedding 和 Neo4j 并注册退出保存，且实现已从仓库删除；
- `get_location` 与拼写如此的 `muti_reply` 在历史中找不到函数定义；
- `read_image` 是已删除 `Chat` 客户端上的绑定方法，当前视觉能力只有
  `image__recognize_image` 一条路径，复活它会形成第二套视觉客户端职责。

要启用其中任何一项，就在 `mods/tools/` 顶层按现有模块格式重新写一个，
再走 `reload_tools` / `load_tools`；不要为了"先留着"把实现搬回本目录。
