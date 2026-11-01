"""One codex exec turn; both new and resumed prompts travel through stdin."""

from . import _process


def run_turn(session, prompt, timeout):
    real_id = session["real_id"]
    argv = ["codex", "exec"]
    # --sandbox 是 exec 自己的选项，必须排在 resume 子命令之前，否则 resume 会报 unexpected argument。
    if session.get("sandbox"):
        argv += ["--sandbox", str(session["sandbox"])]
    elif session.get("full_access") or session.get("danger_full_access"):
        argv += ["--sandbox", "danger-full-access"]
    if real_id:
        argv.append("resume")
    # Default cwd is /tmp, which need not be a Git repository. CLI permission settings still apply.
    argv += ["--json", "--skip-git-repo-check"]
    if session.get("network_access") or session.get("network"):
        argv += ["-c", "sandbox_workspace_write.network_access=true"]
    if session.get("permission"):
        argv += ["--permission", str(session["permission"])]
    if session.get("model"):
        argv += ["--model", session["model"]]
    if real_id:
        argv.append(real_id)
    argv.append("-")
    output, problem = _process.run(argv, session, prompt, timeout)
    texts, completed = [], False
    for event in _process.events(output):
        kind = event.get("type")
        if kind == "thread.started":
            real_id = event.get("thread_id") or real_id
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                texts.append(str(item["text"]))
        elif kind == "turn.completed":
            completed = True
        elif kind == "turn.failed":
            error = event.get("error") or {}
            problem = problem or str(error.get("message") if isinstance(error, dict) else error) or "Codex 本轮失败"
    if not completed:
        errors = [str(event.get("message") or "") for event in _process.events(output)
                  if event.get("type") == "error"]
        problem = problem or "\n".join(filter(None, errors)) or "Codex 没有返回 turn.completed 事件"
    if not real_id:
        problem = problem or "Codex 没有返回 thread_id，下一轮无法续接"
    return real_id, "\n\n".join(texts), not bool(problem), problem
