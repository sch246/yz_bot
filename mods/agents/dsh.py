"""One dsh headless turn, resumed by its real session id."""

import os
from pathlib import Path
import shutil

from . import _process


def run_turn(session, prompt, timeout):
    executable = shutil.which("dsh")
    if executable:
        argv = [executable]
    else:
        # This host has the source checkout launcher used by mods/tools/dsh.py, no dsh shim.
        node = shutil.which("node") or "/root/.nvm/versions/node/v24.22.1/bin/node"
        entry = Path("/root/deepseek-harness/apps/cli/lib/bin.js")
        if not entry.is_file():
            return session["real_id"], "", False, "找不到 dsh 命令或本机 CLI 入口"
        argv = [node, str(entry)]
    argv += ["headless", "--json"]
    real_id = session["real_id"]
    if real_id:
        argv += ["--session-id", real_id]
    argv.append("-")
    # WHY: Bot's DEEPSEEK_BASE_URL may point at /beta; dsh would append the wrong API path.
    env = {key: value for key, value in os.environ.items() if key != "DEEPSEEK_BASE_URL"}
    output, problem = _process.run(argv, session, prompt, timeout, env=env)
    text, final = "", False
    for event in _process.events(output):
        if event.get("type") == "session":
            real_id = event.get("sessionId") or real_id
        elif event.get("type") == "final":
            text, final = str(event.get("text") or ""), True
        elif event.get("type") == "error":
            problem = problem or str(event.get("message") or "dsh 返回错误")
    if not final:
        problem = problem or "dsh 没有返回 final 事件"
    if not real_id:
        problem = problem or "dsh 没有返回 sessionId，下一轮无法续接"
    return real_id, text, not bool(problem), problem
