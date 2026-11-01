"""把活派给本机的 dsh（DeepSeek Harness）：一条任务进去，一段回答出来。

`ask` 走 `dsh headless --json`——开一个新会话、答完就退出，适合一次性的活（读文件给判断、查一段资料、跑一次分析），同步等你拿结果。
`ask_bg` 是同一件事丢到后台：立刻拿到回执，跑完把结果推进你的 me 窗口叫醒你，中途不用干等——跑几分钟甚至半小时的活都该用它。
每次的回答末尾会附一行「会话 session-xxx」，把这个 id 传给下一次的 `session_id`，两次之间就接得上，像同一个窗口接着聊。

**再接上一轮时，workdir 必须和上次一样**（会话头里记着 cwd，对不上它直接报错）；同一个 id 也不能并发问，会撞写锁。

它必须跑在 /tmp 下，并且要清掉 `DEEPSEEK_BASE_URL`（bot 的 .env 把它指向 /beta，dsh 拿它拼出来的地址会 404）。

派活时把边界写死（比如「只给判断，不许改任何文件」）：它默认有读文件、跑命令的权限，说清楚它才不会乱动。
"""

import contextvars
import json
import os
import subprocess

NODE = "/root/.nvm/versions/node/v24.22.1/bin/node"
BIN = "/root/deepseek-harness/apps/cli/lib/bin.js"


def _run_task(task: str, workdir: str, timeout: int, session_id: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_BASE_URL"}
    cmd = [NODE, BIN, "headless", "--json"]
    if session_id:
        cmd += ["--session-id", session_id]
    cmd.append(task)
    try:
        p = subprocess.run(
            cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return (
            f"dsh 超过 {timeout} 秒还没跑完，我把它放掉了。"
            "可以拆小一点再派，或者改用 ask_bg 丢到后台。"
        )

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0 and not out:
        return f"dsh 退出码 {p.returncode}\n--- stderr ---\n{err[-2000:]}"

    text, sid = "", session_id
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        kind = ev.get("type")
        if kind == "session":
            sid = ev.get("sessionId", sid)
        elif kind == "final":
            text = ev.get("text") or text
    if not text:
        text = out

    tail = f"\n\n[会话 {sid}]" if sid else ""
    if p.returncode != 0:
        return f"{text}{tail}\n\n（退出码 {p.returncode}）{err[-500:]}"
    return text + tail


def ask(task: str, workdir: str = "/tmp", timeout: int = 180, session_id: str = "") -> str:
    """把一条任务交给 dsh，等它跑完，返回它的回答（末尾附会话 id）。

    @param
    task: 完整的任务描述，背景、边界、要的输出格式都写进去
    workdir: 在哪个目录里跑，默认 /tmp（在 bot 目录里跑会被 dsh 拒收；接上一轮时要和上次一致）
    timeout: 最多等多少秒，默认 180
    session_id: 上次回答末尾给的那个 id，传了就接着那一轮说；留空表示开新会话
    """
    return _run_task(task, workdir, timeout, session_id)


def ask_bg(
    task: str,
    workdir: str = "/tmp",
    timeout: int = 1800,
    session_id: str = "",
    reason: str = "",
) -> str:
    """把一条任务丢给 dsh 在后台跑，做完把结果推进 me 窗口叫醒你——不干等，可以接着干别的。

    @param
    task: 完整的任务描述，背景、边界、要的输出格式都写进去
    workdir: 在哪个目录里跑，默认 /tmp；接上一轮时要和上次一致
    timeout: 最多让它跑多少秒，默认 1800（半小时）
    session_id: 上次给的那个会话 id，传了就接着那一轮说
    reason: 到时候叫醒你时带的理由，比如「dsh 把 self.md 审完了」
    """
    from mods.tools import _tasks

    scope = contextvars.copy_context()

    def run() -> str:
        return _run_task(task, workdir, timeout, session_id)

    receipt = _tasks.submit("dsh_ask", scope, run, "wake", detached=True)
    if reason:
        receipt += f"（{reason}）"
    return receipt


__all__ = ["ask", "ask_bg"]
