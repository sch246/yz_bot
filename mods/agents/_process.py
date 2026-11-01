"""CLI subprocesses owned by agent windows; timeout and shutdown kill their process groups."""

import json
import os
import signal
import subprocess
import threading


_lock = threading.Lock()
_running: set[subprocess.Popen] = set()
_stopping = False


def _kill(process):
    try:
        # The group leader may already have exited while its children still hold the pipes.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(argv, session, prompt, timeout, *, env=None):
    """Return stdout and a failure description; keep partial stdout even on timeout."""
    with _lock:
        if _stopping:
            return "", "Bot 正在退出，任务没有启动"
        try:
            process = subprocess.Popen(
                argv, cwd=session["cwd"], env=env,
                stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", start_new_session=True,
            )
        except OSError as error:
            return "", f"无法启动 CLI：{error}"
        _running.add(process)
    problem = ""
    try:
        try:
            out, err = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(process)
            out, err = process.communicate()
            problem = f"超过 {timeout:g} 秒，已终止 CLI 进程组"
        if process.returncode and not problem:
            problem = f"CLI 退出码 {process.returncode}"
        if problem and err.strip():
            problem += "\n" + err.strip()[-2000:]
        return out, problem
    finally:
        with _lock:
            _running.discard(process)


def events(output):
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def start():
    global _stopping
    with _lock:
        _stopping = False


def stop():
    global _stopping
    with _lock:
        _stopping = True
        for process in _running:
            _kill(process)
