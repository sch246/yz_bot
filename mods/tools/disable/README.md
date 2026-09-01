# 未启用的历史工具模块

本目录保存历史源码中曾实现、但其 `add_tool` 注册被明确注释掉的工具。
`mods.tools.ToolRegistry` 只扫描 `mods/tools/` 的顶层 `.py` / `.md` 文件，
不会递归进入本目录，所以这里的代码不会出现在 last-good registry，也不会
被任何 Chat 激活。不要把“源码已整理”理解成“工具已开放”。

目录中的 `.py` 都遵循正常工具模块格式，并把运行依赖延迟到函数调用时：

| 模块 | 已整理工具 | 语义来源 |
|---|---|---|
| `context_data.py` | `read_data` | 动态环境的 `data`，现由 `storage.get("", "py_data")` 唯一持有 |
| `group_info.py` | `group_size`, `group_members` | 当前群的实时成员列表 |
| `calendar_fortune.py` | `lunar_date`, `xiaoliu` | 当前 `mods.lunar` 的历法与六态算法 |
| `send_message.py` | `sendmsg` | 当前窗口或显式群/私聊目标的异步发送队列 |
| `later_manage.py` | `later_list`, `later_set` | 当前窗口的持久延时任务命令 |
| `image_url.py` | `url2cq` | 当前共享的图片下载和 CQ 转换路径 |
| `baidu.py` | `baidu_encyclopedia` | 历史第三方百科接口 |

两处明显无效的历史接口没有继续伪装成可用能力：

- `later_set` 旧签名中的 `code` 从未进入实际命令，当前延时任务也只保存
  `expr`；整理后的签名删除了 `code`。
- `sendmsg` 的旧说明称 `user_id` 与 `group_id` 冲突，却会把两者一起交给
  发送层；整理后的函数明确拒绝同时提供两个目标。返回“已发送”仍只表示
  已排队，不承诺最终送达。

## 没有迁成函数的名字

- `rag_search` 只有一段被整体注释的函数草稿。它引用的旧 HippoRAG 模块
  会在导入时初始化 LLM、embedding 和 Neo4j 并注册退出保存，随后整套实现
  已从仓库删除；现有证据不足以恢复一个可运行、无副作用的惰性工具，因此
  本目录不伪造同名空壳。
- `get_location` 与拼写如此的 `muti_reply` 只有注释掉的注册名，历史中没有
  找到函数定义。
- `read_image` 并非纯死名字：旧实现是已删除 `Chat` 客户端上的绑定方法，
  直接调用当时的 OpenAI vision completion。当前图片识别已有唯一的
  `recognize_image` 路径；复制旧方法会形成第二套视觉客户端职责，所以未迁。

若未来决定启用某项能力，应把对应文件移动或合并到 `mods/tools/` 顶层，
再按 registry 的显式 `reload_tools` / `load_tools` 流程应用。单纯编辑本目录
永远不会改变运行中的 Bot。
