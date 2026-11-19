用 rmcp 跨机器执行命令、读写传文件与管理远端后台进程（支持 Linux/Windows 与多跳路由）。

本机 `PATH` 已装好零依赖单文件工具 `rmcp`（`/root/.local/bin/rmcp`）。需要查看、巡检或操作其它机器（尤其是 Windows 远端主机，或者需要经过跳板机多跳访问的内网机器）时，优先用 `rmcp`，不要手搓多层 `ssh` 引号转义或临时 `.ps1` 脚本——`rmcp` 底层走 Base64 帧封装并强制远端 UTF-8，中文路径、复杂引号和跨平台换行都不会乱码。

## 会话隔离机制（放心用，不会跟外部串台）

- **全机共享节点拓扑**：`router.json` 里的已知节点（先跑一句 `rmcp :nodes` 就能看到当前配好的所有机器别名，如 `win4`、`win6` 等）是全机共享的，连过一次就能直接用。
- **按调用源头自动隔离会话**：`rmcp` 会按「本机 `node_id` + 你所在的仓库根目录 `/root/bot`」把你的当前默认目标（`:use`）和各远端机器上的工作目录（`:cd`）单独存进你专属的 `sessions/<hash>.json`（哪怕在 `/root/bot/mods` 子目录里调用也自动归属 `/root/bot`）。你在外面并行的其它 Agent 或人工终端各有各的会话文件，互不覆盖。
- **远端进程带源头署名**：所有请求跨跳都会带上你的 `origin`（`主机:/root/bot`），在远端起后台进程后调 `:ps` 能直接看到 `"caller"` 字段，杀进程前核对一下归属，别误杀别人起的任务。

## 怎么配合你手里的工具用

1. **常规命令、读写文件、长耗时构建 —— 直接走 `host__run_command`**
   - 单次指派某台机器跑命令（不改默认目标）：
     `rmcp --to <节点名> "<远端命令>"`
   - 连续操作同一台机器（只改你 `/root/bot` 自己的默认目标和远端目录）：
     `rmcp :use <节点名>` -> `rmcp :cd D:/目标目录` -> `rmcp git status -sb`
   - 如果远端命令超过 15 秒，`host__run_command` 会自动帮你转后台并在跑完时叫醒 `me` 窗口，不用额外包别的异步逻辑。
   - **千万别在 `host__run_command` 里敲 `rmcp -i` 或 `rmcp :repl`**（非交互管道没喂输入会直接结束或干等）；单次命令直接传参就行。

2. **需要连续多轮高频交互（如跨多跳反复试探） —— 走 `term:` 持久终端**
   - 如果要保持多跳 SSH 管道常驻热复用（`--stdio-loop`，免去每次重新握手），在 `term:rmcp` 窗口里运行 `rmcp :repl <节点名>`：
     - 窗口里直接敲远端命令，用 `!<本地命令>` 临时跑本机命令，`:use <另一节点>` 秒切机器，用完发 `:q` 退出。

## 常用命令速查

```bash
# 查拓扑与连通性
rmcp :nodes                                          # 看有哪些已知节点、路由表、你当前的 active 与远端 cwd
rmcp :sessions                                       # 看本机各调用源头的独立会话状态
rmcp :ping <节点>                                     # 测连通性并查看实际经过的 via 跳数路径
rmcp :env <节点>                                      # 看远端系统、Shell、Python、磁盘空间快照

# 跨任意多跳读写改与传大文件（相对路径自动按你当前跟踪的远端 cwd 解析，端到端 SHA-256 校验）
rmcp --to <节点> :read README.md
rmcp --to <节点> :write note.txt "直接写中文内容"
rmcp --to <节点> :edit config.toml "旧配置" "新配置"
rmcp --to <节点> :upload ./local.zip D:/releases/local.zip
rmcp --to <节点> :download D:/logs/app.log /tmp/app.log

# 远端脱离式后台进程（跨单次 SSH 调用持久记录，带 caller 溯源）
rmcp --to <节点> :spawn python -m http.server 18080 --stdout D:/logs/srv.log
rmcp --to <节点> :ps                                  # 查看运行状态与 caller 归属
rmcp --to <节点> :kill <pid>

# 新增节点连接（首次连 SSH 节点加 --bootstrap 会自动把单文件 rmcp.py 部署过去；多跳加 --via <跳板>）
rmcp :connect <新别名> ssh://user@host --bootstrap
rmcp :connect <内网别名> ssh://user@192.168.x.x --via <跳板节点> --bootstrap
```

## 注意事项

- **Windows 远端 Shell 语法**：目标机是 Windows 时，普通命令默认交给远端 `cmd.exe` 执行——命令串联用 `&&` 或 `&`（不要用 `;`），没有 `head`/`tail`/`wc` 时直接跑 `python -c "..."` 或 `powershell -Command "..."`（有 `rmcp` 帧保护，双引号和 `$` 不会被 SSH 中途吃掉）。
- **激活后提醒**：如果你刚新建或改过本文件，先 `reload_tools(["rmcp"])` 再 `load_tools(["rmcp"])`。
