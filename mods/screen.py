"""GNU screen helpers used by trusted device features."""

import os
import subprocess
import time


_started: set[str] = set()


def _logpath(name: str) -> str:
    return f"data/screens/{name}.0"


def check(name: str = None):
    if name is None:
        result = subprocess.run(
            ["screen", "-v"], capture_output=True, text=True, check=False
        )
        return "version" in (result.stdout + result.stderr).lower()
    result = subprocess.run(
        ["screen", "-ls"], capture_output=True, text=True, check=False
    )
    return "\n".join(line for line in result.stdout.splitlines() if f".{name}" in line)


def rel_exec(path: str, command: str) -> None:
    """Run a trusted shell command relative to *path*, matching the old helper."""
    subprocess.call(command, cwd=path, shell=True)


def start(name: str):
    if check(name):
        return "已存在同名screen"
    os.makedirs("data/screens", exist_ok=True)
    result = subprocess.run(
        ["screen", "-L", "-Logfile", _logpath(name), "-dmS", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return result.stderr.strip() or f"screen 启动失败: {result.returncode}"
    _started.add(name)
    return f"已启动: {name}"


def pop(name: str):
    path = _logpath(name)
    try:
        with open(path, encoding="utf-8") as log_file:
            text = log_file.read()
        with open(path, "w", encoding="utf-8"):
            pass
    except FileNotFoundError:
        return ""
    return text


def send(name: str, command: str):
    os.makedirs("data/screens", exist_ok=True)
    with open(_logpath(name), "w", encoding="utf-8"):
        pass
    result = subprocess.run(
        ["screen", "-S", name, "-X", "stuff", command + "\n"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return result.stderr.strip()
    time.sleep(0.2)
    return pop(name)


def _trusted_send(name: str, command: str):
    from mods import context, op

    if not op.is_op(context.current()):
        return "无权限"
    return send(name, command)


def vcs(command: str = "") -> str:
    result = _trusted_send("vcs", command)
    if result == "无权限":
        return result
    lines = result.splitlines()[1:]
    return "\n".join(lines).replace("\x1b[?2004h", "").replace("\x1b[?2004l", "")


def iex(command: str = ""):
    return _trusted_send("iex", command)


def log(name: str, start: int = None, end: int = None):
    try:
        with open(_logpath(name), encoding="utf-8") as log_file:
            return "".join(log_file.readlines()[start:end])
    except FileNotFoundError:
        return ""


def stop(name: str):
    try:
        with open(_logpath(name), "w", encoding="utf-8"):
            pass
    except FileNotFoundError:
        pass
    result = subprocess.run(
        ["screen", "-S", name, "-X", "quit"],
        capture_output=True,
        text=True,
        check=False,
    ).returncode
    _started.discard(name)
    return result


def on_exit() -> None:
    for name in list(_started):
        try:
            stop(name)
        except OSError:
            # Continue closing other sessions if screen disappeared mid-run.
            _started.discard(name)
