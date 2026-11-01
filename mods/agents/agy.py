"""One agy print turn, resumed by conversation_id."""

import json

from . import _process


def run_turn(session, prompt, timeout):
    real_id = session["real_id"]
    # agy ignores stdin when -p supplies a prompt; '-' is not a documented stdin marker.
    argv = ["agy", "-p", prompt, "--model", session.get("model") or "gemini-3.8-flash-high",
            "--output-format", "json"]
    if real_id:
        argv += ["--conversation", real_id]
    output, problem = _process.run(argv, session, None, timeout)
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError("not an object")
    except ValueError:
        return real_id, "", False, problem or "agy 没有返回有效的 JSON 对象"
    real_id = result.get("conversation_id") or real_id
    text = str(result.get("response") or "")
    status = str(result.get("status") or "").lower()
    if status != "success":
        problem = problem or f"agy 状态为 {status or '空'}：{result.get('error') or '本轮未完成'}"
    if not real_id:
        problem = problem or "agy 没有返回 conversation_id，下一轮无法续接"
    return real_id, text, not bool(problem), problem
