# 异常、日志与 link 报错节流

> 状态：未实现提案

## 一个克制的应用 logger

只保留一个应用 logger，删除 `s3.__logging`、`s3.log` 和散落 `print` 的重复职责：

- `ERROR`：操作、模块或必要边界失败，关联 incident；
- `WARNING`：降级、重试、配置拒绝或可恢复异常；
- `INFO`：启动/停止、模块加载汇总、模式切换和少量重要状态；
- `DEBUG`：中间值和诊断采样，仅 `-d` 开启。

默认终端显示 warning/error 和极少 core 生命周期摘要；滚动应用文件保存 info 以上，debug 模式扩展。按日滚动并限制保留天数，当前 7 天可作默认。聊天正文只进入 chatlog，不复制到应用日志或终端；完整 traceback 只记录一次，终端和聊天显示摘要与 incident ID。

`report_exception(phase, component, exc, msg, fatal)` 生成短 incident ID，通过唯一 logger 写入时间、阶段、组件、异常类型、消息、完整 traceback 和最小聊天定位。不要记录正文、token、密钥、完整请求体或整条 OneBot JSON。

各失败边界保留不同策略：

| 边界 | 候选策略 |
|---|---|
| core 必要依赖 | 记录并终止 |
| feature 初始化 | 标为 failed，其它 feature/core 继续 |
| 可选命令导入 | 禁用插件并移出候选表 |
| 单条消息/命令 | 记录并向原窗口返回 incident，主循环继续 |
| 后台线程/Future | 捕获处记录，再把异常放入 Future |
| storage 热加载 | 保留最后有效内存，等待修复 |
| storage 写盘 | 保留 dirty/baseline 并重试 |

## Link 当前行为与候选行为

当前实现：

- cond 异常会把 traceback 发到聊天，返回 falsy，随后沿 `fail` 继续；
- action 异常会把 traceback 发到聊天，但节点仍可能被当作成功并继续 `succ`；
- 宽泛 link 可以让每条普通消息重复触发同一 traceback，破坏聊天可用性。

候选行为必须明确区分：cond 异常留下 incident 后沿 fail 继续，让其它反应仍可工作；action 异常停止本节点 action 和成功后继，不把半执行状态当成功。

## 聊天提醒节流

错误证据与聊天提醒是两种策略。异常证据进入 logger；聊天层用近期消息窗口决定是否再提醒。Link key 至少包含 `(聊天窗口, link 名称, cond/action 阶段, 异常 fingerprint)`：首次发送简短 incident，相同 fingerprint 在近期 256 条消息内不再发言，不同错误立即提示；退出窗口后仍可再次提醒。被抑制次数可在后续摘要或维护命令查看。

权限不足等既有提醒仍由靠近 cache 的 `send_once_in_recent(...)` 处理。不要让 logger import cache，也不要用一个 Throttle 强行统一时间窗口、256 条消息窗口和用户/命令作用域。

Link 另有进程级聊天报错总开关，默认开启：

```text
.link error status
.link error off
.link error on
```

关闭只静音聊天 incident；日志、link 执行和失败控制流不变。第一版重启恢复开启，避免调试静音永久隐藏故障。关闭期间累计 fingerprint 次数，重新开启只发汇总，不补发全部历史错误。

## 最小实施顺序

1. 统一应用 logger 并停止 chatlog 复读到终端；
2. 覆盖启动、storage、命令导入、主消息边界和 `to_thread`；
3. 给 link cond/action 传入节点名和阶段，建立 incident；
4. 加入近期 256 条聊天提醒节流；
5. 加入 link 全局聊天报错开关；
6. 删除第二套 logger 和对应兼容调用。
