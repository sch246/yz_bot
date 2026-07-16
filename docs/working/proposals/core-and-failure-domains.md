# Core 与纵向失败域

> 状态：未实现提案

恢复能力不需要 normal/safe 两套启动程序。目标是一个被明确假定可靠的 core，加上一组按业务能力纵向切分的 feature。core 只拥有 OneBot 基础收发、普通 dict 判定、管理员身份、最小 storage/config 读取、cmd 加载器、错误报告、模块状态和生命周期命令。core 失败就是真正无法在线恢复；这个假设边界必须很小并受到最严格验证。

## 失败域按用户能力纵向切分

core 外的模块按照“失败后应一起废弃哪些功能”切分，而不是按 controller/service/model 等技术层横切。例如：

- LLM feature 包含客户端、聊天状态、工具和相关命令；
- Minecraft feature 包含 screen/RCON、启停和 `.mcf`；
- 媒体 feature 包含图片下载、转换及其命令。

一个 feature import 失败会使依赖它的命令一并加载失败，但不应阻止命令加载器、`.reboot` 和其它 feature 工作。所谓安全状态只是“core 在线、部分 feature failed”，不是第二套入口或平行命令系统。

`main` 仍是显式 composition root：先建立 core，再按清单逐个 `load_feature()`，最后加载现有 cmds 并进入同一消息循环。模块只提供普通数据和函数，由入口显式组合；不引入依赖注入容器或自动依赖图。加载结果只需 `loaded/failed/disabled` 三种状态和对应 incident，清单顺序继续承担人工依赖编排。

## 对当前反向引用架构的迁移

大量模块 `from main import ...`，整个 `funcs.py` 又在 cmds 前一次性注入全局名称。直接拆目录会把一个启动失败变成大量连锁 NameError，因此采用纵向切片：

1. 先修复 cmds 的局部真实缺陷：失败命令不可调用；只有决定让 `.py` 成为可降级 feature 时，路由才不再硬取 `.py`；
2. 从 `funcs.py` 只抽出真正不可缺的 core 函数，其余代码暂时由显式 feature loader 导入；
3. 选择一个边界清楚的 feature，使其命令直接依赖该 feature 模块，而不是反向依赖 `main`；
4. 验证后删除 `main` 中对应兼容导出，再迁移下一个 feature；
5. 最终删除 `from funcs import *` 作为全局启动前提。

过渡期允许成功加载的 feature 暂时把名称发布到 `main`，只供尚未迁移的命令使用。删除条件是所有消费者已经改为显式依赖 feature；新代码不得继续依赖这个兼容出口。

## 动态执行环境的收束方向

当前 `.py`/link 名称来自 `main`、`funcs.py`、`.py` 模块 globals、`loc` 和 `data/pyload.py`。目标不是给它们再套一层容器，而是明确一个运行期执行环境和几个可审计的安装入口。

`pyload.py` 的即时持久化体验必须保留。更好的上位替代可以是一个原子操作：把定义写入受版本控制的 `funcs.py` 或正式 feature 模块，同时把同一份已验证定义安装到当前执行环境。只改源码会失去立即生效，只改内存会失去重启恢复，两者都不是等价替代。

## 最小验收

- 一个非核心 feature 导入失败时，Bot 仍能接收消息并执行 core/其它命令；
- 失败命令不会留在可匹配候选表；
- 若本提案选择把 `.py`/link 移出 core，它们失败时不会使所有事件在路由入口 KeyError；
- 模块状态命令能显示 loaded/failed/disabled 和 incident；
- 每完成一个 feature 迁移，就删除对应的 `main` 兼容导出。
