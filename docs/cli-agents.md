# CLI agent 窗口

`dsh`、`agy`、`codex` 是三种可发送的聊天窗口。目标是人为起的别名，例如
`dsh:main`、`agy:review`、`codex:code`。用现有输入框发送：

```python
type_word("dsh:main", "读一下当前目录的 README，告诉我这个项目做什么。不要改文件。")
# 下一步，看过新到的消息以后：
send("dsh:main", wait=-1)
```

发送立即返回 8 位 hex 消息号；整段输入（包括空行）作为一次 CLI 任务。
同一窗口还在运行时再发，会提示等待，草稿留在输入框。不同窗口可以同时跑。
完成或失败后，结果都写回原窗口，以 CLI 名字作作者，并以 `dsh:main 回复了` 这样的理由
唤醒中心 agent；已有的 mute 覆盖层仍然有效。发送时写 `wait="10m"` 会在这个窗口挂一次等待，
回答到达时认领它，唤醒原因里同时写“等到了”。`read_log("dsh:main")` 翻历史，
`search("关键词", window="dsh:main")` 搜该窗口。

普通模块在 Bot 下次完整启动时加载。本次源码改动不让正在运行的进程自动切换，也不执行重启。

## 会话配置

`data/agents/sessions.json` 是可人工编辑的 JSON，第一次发送时自动创建。它按**别名**索引，
所以不同 CLI 也不能共用一个别名；需要两个主窗口时可用 `main` 和 `codex-main`。
每次发送及结果回写时都会重新读文件，修改配置无需额外加载命令。

首次发送前可以写入：

```json
{
  "main": {
    "kind": "dsh",
    "target": "main",
    "real_id": "",
    "cwd": "/tmp"
  },
  "review": {
    "kind": "agy",
    "target": "review",
    "real_id": "",
    "cwd": "/root/bot",
    "model": "gemini-3.8-flash-high",
    "timeout": 1800
  },
  "code": {
    "kind": "codex",
    "target": "code",
    "real_id": "",
    "cwd": "/root/bot"
  }
}
```

- `real_id`：首轮留空；每轮结束后保存 CLI 返回的真实会话号，续轮自动使用。
- `cwd`：已存在的绝对目录，默认 `/tmp`。dsh 续轮要求与原会话目录一致；换目录应另起别名或清空 `real_id`。
- `model`：agy 默认 `gemini-3.8-flash-high`；Codex 不填则使用本机 CLI 配置。dsh 使用它自己的配置。
- `timeout`：每轮最多多少秒，默认 1800，必须大于 0；超时对本轮进程组执行 `SIGKILL`。
- `created_at`、`updated_at`：自动写入的 Unix 秒时间戳。

不要在运行中改 `kind`、`target`、`real_id` 或 `cwd`；结果回写发现这些字段变了会报错并保留磁盘配置。
损坏 JSON 会阻止发送，不会被空配置覆盖。清空 `real_id` 会新开 CLI 会话，窗口原有聊天记录仍保留。

## 本机 CLI 与记录

dsh 优先用 PATH 上的 `dsh`，没有时用本机现有源码入口；它和 Codex 的 prompt 都走 stdin。
agy 的 `-p` 模式会忽略 stdin，因此 prompt 走 argv，特别长的输入仍受系统参数长度限制。
Codex 续轮用 `codex exec resume <real_id> -`，新轮与续轮都加 `--json --skip-git-repo-check`，
这样默认 `/tmp` 不需要是 Git 仓库。JSONL 事件按 [Codex 非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode) 解析。

CLI 使用本机已有的登录与权限配置；配置 `cwd` 只改变工作目录，不授予 CLI 额外权限。
发送入口要求 Bot 自身具有 op 权限。dsh 会清除子进程环境里的 `DEEPSEEK_BASE_URL`，
避免 Bot 的 API 地址覆盖 dsh 的配置；不修改 Bot 环境或 `mods/tools/dsh.py`。

聊天记录继续使用 `mods.chatlog.rootfile`：当前默认是 `chatlog/<kind>/<别名>/年-月/日.log`，
若运行环境将根目录设为 `data/chatlog`，路径反查和窗口枚举同样适用。QQ 的历史恢复及 NapCat
离线补回仍只处理 group/private。CLI 窗口的 Markdown 标题不会被当作 QQ `#` 控制消息隐藏。

正常退出会终止本模块启动的 CLI 进程组，记录失败但不再唤醒模型。首轮的真实会话号在整轮结束后
才写回；若 Bot 被强杀或断电，正在运行的首轮可能没有留下映射，也不会自动重放任务。
