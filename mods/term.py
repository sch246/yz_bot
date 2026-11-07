"""长期命令行窗口：一个 tmux 会话就是一个窗口 ``term:<名字>``，输出攒成一批一批推进来。

她用 send 往 ``term:main`` 写一整段，程序把它原样粘进会话再回车；第一次发就开一个
新会话（工作目录是 Bot 的运行目录）。会话的输出由 tmux 的 pipe-pane 追加到
``data/term/<名字>.log``，读线程每 0.25 秒看一眼，停顿 1 秒或攒满 10 秒，就把这一批去掉控制
字符、作为一条记录 push 进窗口；作者写此刻前台的程序（bash、make、python3），她看记录头就
知道命令还在不在跑。她敲的那行的回显不进记录，它已经记成她自己的一条。一条记录太长时留开头
一点和结尾，往前翻用 ``term__look``。

叫醒：她发完以后，前台回到 shell，或者回到她敲字时的那个程序（python 的 ``>>>`` 这类），
那一批叫醒她；shell 退出、会话没了也叫醒。没人问时的输出（服务器日志）只记成未读，要每批
都叫醒就 follow。起服务器、进 REPL 这种前台换了程序的，用 send 的 wait 等第一批输出，或者
等输出里出现某段文字（send 的 who，或 ``wake_me("wait term:main「Listening」 10m", …)``）。

WHY: 用 tmux 而不是 GNU screen（维护者 2026-11-02 问 screen 是不是过时了）。pipe-pane 当场
写出，screen 的 ``-L`` 日志默认攒十秒才落盘；load-buffer/paste-buffer 按字节原样粘贴，
screen 的 stuff 会解释 ``^`` 和反斜杠（``find … -exec … \\;`` 会被改掉）；pane_current_command
直接说前台是谁、pane_dead_status 给退出码，不用靠认提示符；capture-pane 给渲染后的屏幕，
vim、top 这类整屏程序也看得懂。旧的 ``mods.screen``（mc、nl 在用）没有动。
"""

from __future__ import annotations

import codecs
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from uuid import uuid4

from mods import FEATURE, storage


PHASE = FEATURE
LOAD_AFTER = ("chat", "op", "storage")
KIND = "term"
ROOT = "data/term"
# WHY: 停顿 1 秒、最长 10 秒是设计文档 18.2 定的攒批规则。看到提示符也照样等这 1 秒：维护者
# 2026-11-01 说立即提交会误判，这里根本不认提示符，叫不叫醒看前台是谁。
IDLE = 1.0
MOST = 10.0
POLL = 0.25
RECORD_MAX = 2000
HEAD = 400
READ_MAX = 1 << 20
BUFFER_MAX = 64 * 1024
LOG_MAX = 20 * 1024 * 1024
LOOK_MAX = 8000
SHELLS = frozenset({"bash", "sh", "zsh", "fish", "dash", "ash", "ksh", "tcsh", "csh"})
_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
_KEY = re.compile(r"(?:[CMS]-)*(?:[A-Za-z][A-Za-z0-9]*|[0-9]|[^\s;\\])")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
                   r"|\x1b[PX^_][^\x1b]*\x1b\\|\x1b[()][0-9A-Za-z]|\x1b[@-Z\\-_=>]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CONF = "set -g history-limit 10000\nset -g remain-on-exit on\n"

_lock = threading.RLock()
_live: dict[str, dict] = {}
_reader: threading.Thread | None = None
_stopping = threading.Event()
_log = logging.getLogger(__name__)


def _state() -> dict:
    """Per terminal: the log offset already pushed and the input still waiting for an answer."""
    return storage.get("term", "state")


def _socket() -> str:
    # WHY: 她的终端用自己的 tmux 服务器，和维护者自己开的 tmux 分开，名字不会撞；临时运行
    # 目录（smoke、合成脚本）各有各的 socket，碰不到设备上真在跑的会话。
    return os.path.abspath(os.path.join(ROOT, "tmux.sock"))


def _logpath(name: str) -> str:
    return os.path.abspath(os.path.join(ROOT, f"{name}.log"))


def _target(name: str) -> str:
    return f"={name}:"


def _tmux(*args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    conf = os.path.join(ROOT, "tmux.conf")
    result = subprocess.run(["tmux", "-f", conf, "-S", _socket(), *args], input=stdin,
                            capture_output=True, text=True, timeout=10)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"tmux {args[0]} 失败（{result.returncode}）")
    return result


def available() -> bool:
    try:
        return subprocess.run(["tmux", "-V"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _name(window) -> str:
    kind, name = tuple(window)
    if kind != KIND or not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("命令行窗口写 term:main 这样；名字只用字母、数字、下划线、横线，最长 32")
    return name


def _panes() -> dict[str, dict] | None:
    """Each session's active pane; ``None`` when tmux itself could not be asked this time."""
    try:
        result = _tmux("list-panes", "-a", "-F",
                       "#{session_name}\t#{window_active}#{pane_active}\t#{pane_dead}\t"
                       "#{pane_dead_status}\t#{pane_current_command}", check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        problem = result.stderr
        return {} if ("no server running" in problem or "error connecting" in problem) else None
    panes = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 5 and (parts[1] == "11" or parts[0] not in panes):
            panes[parts[0]] = {"dead": parts[2] == "1", "status": parts[3], "fg": parts[4]}
    return panes


def _clean(text: str) -> str:
    """Raw pty bytes as a person would read them: no escapes, progress bars at their last state."""
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    lines = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if "\r" in line:
            line = line.rsplit("\r", 1)[1]
        if "\b" in line:
            kept: list[str] = []
            for char in line:
                if char == "\b":
                    if kept:
                        kept.pop()
                else:
                    kept.append(char)
            line = "".join(kept)
        lines.append(_CONTROL.sub("", line).rstrip())
    return "\n".join(lines).strip("\n")


def _cap(text: str, name: str, keep: str | None = None) -> str:
    if len(text) <= RECORD_MAX:
        return text
    head = text[:HEAD]
    head = head[:head.rfind("\n")] if "\n" in head else head
    tail = text[-(RECORD_MAX - HEAD):]
    tail = tail[tail.find("\n") + 1:] if "\n" in tail else tail
    dropped = len(text) - len(head) - len(tail)
    back = f"往前翻用 term__look(\"{name}\", lines=200)，最近的原始输出在 data/term/{name}.log"
    middle = f"……（中间省略 {dropped} 字；{back}）……"
    # WHY: wait 是拿记录正文找要等的文字；它若落在省略的中间，那一行要留下，否则永远等不到。
    if keep and keep in text and keep not in head and keep not in tail:
        at = text.find(keep)
        start = max(text.rfind("\n", 0, at) + 1, at - 200)
        end = text.find("\n", at + len(keep))
        end = min(len(text) if end < 0 else end, at + len(keep) + 200)
        middle = f"……（中间省略 {dropped} 字，你等的那段在这里）……\n{text[start:end]}\n……（{back}）……"
    return f"{head}\n{middle}\n{tail}"


def _fresh(offset: int) -> dict:
    return {"offset": offset, "text": "", "first": None, "last": 0.0,
            "decoder": codecs.getincrementaldecoder("utf-8")(errors="replace")}


def _read_new(name: str, live: dict) -> None:
    path = _logpath(name)
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return
    if size < live["offset"]:
        live["offset"] = 0
    if size == live["offset"]:
        return
    start, note = live["offset"], ""
    if size - start > READ_MAX:
        note = f"\n（这里有 {size - READ_MAX - start} 字节没读）\n"
        start = size - READ_MAX
        live["decoder"].reset()
    with open(path, "rb") as file:
        file.seek(start)
        data = file.read(size - start)
    live["offset"] = start + len(data)
    text = note + live["decoder"].decode(data)
    if len(data) and live["offset"] >= LOG_MAX:
        # WHY: cat 以追加方式写，清空后照样从头写；清空前的已经读进来了。只留最近的原始输出。
        os.truncate(path, 0)
        live["offset"] = 0
    if text:
        now = time.time()
        buffer = live["text"] + text
        if len(buffer) > BUFFER_MAX:
            buffer = (buffer[:BUFFER_MAX // 8] + f"\n（这里有 {len(buffer) - BUFFER_MAX} 字没留）\n"
                      + buffer[-(BUFFER_MAX * 3 // 4):])
        live.update(text=buffer, last=now, first=live["first"] or now)


def _flush(name: str, live: dict, fg: str, *, quiet: bool = False, ending: str = "", reason: str = "") -> None:
    """Push what has piled up as one record, waking her when it answers her input."""
    from mods import chat
    from mods.chat import awaker

    text = _clean(live["text"])
    live.update(text="", first=None)
    state = _state().setdefault(name, {})
    state["offset"] = live["offset"]
    pending = state.get("pending")
    if pending and pending.get("echo") and text:
        # WHY: 回显已经记成她自己的一条；留着它，wait 要等的文字若也写在命令里（make && echo 完成），
        # 自己的回显就先把等待认领了。只在发完后的第一批里找最后一行，多行输入的前几行回显照旧留着。
        echo = pending.pop("echo")
        lines = text.split("\n")
        for index, line in enumerate(lines[:8]):
            if line.rstrip().endswith(echo):
                text = "\n".join(lines[index + 1:]).strip("\n")
                break
    if ending:
        text = (text + "\n" if text else "") + ending
    if not text.strip():
        return
    wake = reason
    if reason:
        state["pending"] = None
    elif not quiet and pending and fg and (fg in SHELLS or fg == pending.get("fg") or pending.get("fg") == "*"):
        wake = f"term:{name} 的命令跑完了" if fg in SHELLS else f"term:{name} 的 {fg} 回话了"
        state["pending"] = None
    body = _cap(text, name, awaker.waiting_text((KIND, name)))
    chat.push((KIND, name), author=fg or "终端", text=body, wake=wake)


def _bury(name: str, pane: dict | None) -> None:
    live = _live.pop(name, None)
    if pane is None:
        ending, reason = "（tmux 会话不见了；再往这个窗口发命令会开一个新的）", f"term:{name} 的会话不见了"
    else:
        ending = f"（shell 退出了，退出码 {pane['status'] or '?'}；再往这个窗口发命令会开一个新的）"
        reason = f"term:{name} 的 shell 退出了"
    try:
        if live is not None:
            _read_new(name, live)
            _flush(name, live, pane["fg"] if pane else "", ending=ending, reason=reason)
    finally:
        _state().pop(name, None)
        _tmux("kill-session", "-t", f"={name}", check=False)


def _tick() -> None:
    with _lock:
        if not _live:
            return
        panes = _panes()
        now = time.time()
        for name, live in list(_live.items()):
            pane = panes.get(name) if panes is not None else {"dead": False, "fg": "", "status": ""}
            if pane is None or pane["dead"]:
                _bury(name, pane)
                continue
            _read_new(name, live)
            if live["text"] and (now - live["last"] >= IDLE or now - live["first"] >= MOST):
                _flush(name, live, pane["fg"])


def _loop() -> None:
    while not _stopping.wait(POLL):
        try:
            _tick()
        except Exception:
            _log.exception("读命令行窗口的输出出错")


def _watch(name: str, offset: int) -> None:
    global _reader
    _live[name] = _fresh(offset)
    if _reader is None or not _reader.is_alive():
        _stopping.clear()
        _reader = threading.Thread(target=_loop, name="term-reader", daemon=True)
        _reader.start()


def _prepare_root() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conf = os.path.join(ROOT, "tmux.conf")
    if not os.path.exists(conf):
        with open(conf, "w", encoding="utf-8") as file:
            file.write(_CONF)


def _open(name: str) -> None:
    """Start a session with its output piped to the log, and wait until the shell has spoken."""
    path = _logpath(name)
    offset = os.path.getsize(path) if os.path.exists(path) else 0
    _tmux("new-session", "-d", "-s", name, "-x", "160", "-y", "48", "-c", os.getcwd(), ";",
          "pipe-pane", "-o", "-t", _target(name), "cat >> " + shlex.quote(path))
    # WHY: 刚开的 shell 还没打出提示符就粘贴，终端会把这段先回显一遍、提示符后再显示一遍
    # （2026-11-02 在容器里试出来的）。等它第一次有输出，最多 3 秒；这只是等 shell 起来，
    # 不是认提示符。
    deadline = time.time() + 3
    while time.time() < deadline and (not os.path.exists(path) or os.path.getsize(path) <= offset):
        time.sleep(0.05)
    # The banner and first prompt are not an answer to anything; keep them out of the window.
    offset = os.path.getsize(path) if os.path.exists(path) else offset
    _watch(name, offset)
    _state()[name] = {"offset": offset, "pending": None}


def _ensure(name: str) -> dict:
    if not available():
        raise RuntimeError("这台机器没装 tmux，开不了命令行窗口")
    _prepare_root()
    if name not in _live or _tmux("has-session", "-t", f"={name}", check=False).returncode:
        _live.pop(name, None)
        _open(name)
    return _live[name]


def send(window, text: str) -> str:
    """Paste *text* into the terminal and press Enter; returns a receipt id at once."""
    from mods import chatlog, context, op

    if not op.bot_is_op():
        raise PermissionError("命令行能操作本机，需要 Bot 自身开着 op 权限")
    name = _name(window)
    body = str(text).rstrip("\n")
    if not body.strip():
        raise ValueError("要敲进命令行的话不能为空")
    with _lock:
        live = _ensure(name)
        panes = _panes() or {}
        fg = (panes.get(name) or {}).get("fg", "")
        # 敲字前已经到的输出先单独成一条、不叫醒，免得被当成这次的回答。
        _read_new(name, live)
        if live["text"]:
            _flush(name, live, fg, quiet=True)
        _state().setdefault(name, {})["pending"] = {"fg": fg, "at": time.time(),
                                                    "echo": body.split("\n")[-1].strip()}
        record_id = uuid4().hex[:8]
        with context.window_lock((KIND, name)):
            chatlog.append_record(KIND, name, "我", body, time.time(), record_id)
        buffer = f"yz-{name}"
        _tmux("load-buffer", "-b", buffer, "-", ";", "paste-buffer", "-d", "-b", buffer, "-t", _target(name),
              ";", "send-keys", "-t", _target(name), "Enter", stdin=body)
    return record_id


def keys(name: str, names: str) -> str:
    """Press tmux key names (``C-c``, ``Up``, ``q``) in a running terminal; the next batch wakes her."""
    from mods import op

    if not op.bot_is_op():
        raise PermissionError("命令行能操作本机，需要 Bot 自身开着 op 权限")
    name = _name((KIND, name))
    tokens = str(names).split()
    if not tokens or any(not _KEY.fullmatch(token) for token in tokens):
        raise ValueError("按键写 tmux 的键名，空格分隔，比如 C-c、Up、Enter、Tab、Escape、q")
    with _lock:
        if name not in _live:
            raise ValueError(f"term:{name} 没开着")
        _state().setdefault(name, {})["pending"] = {"fg": "*", "at": time.time()}
        _tmux("send-keys", "-t", _target(name), *tokens)
    return f"按了 {' '.join(tokens)}"


def look(name: str, lines: int = 0) -> str:
    """The rendered screen of a terminal, or its last *lines* of scrollback."""
    name = _name((KIND, name))
    with _lock:
        if name not in _live:
            raise ValueError(f"term:{name} 没开着")
        args = ["capture-pane", "-p", "-J", "-t", _target(name)]
        if lines > 0:
            args += ["-S", f"-{int(lines)}"]
        text = _clean(_tmux(*args).stdout)
        pane = (_panes() or {}).get(name) or {}
    if len(text) > LOOK_MAX:
        text = f"……（前面还有 {len(text) - LOOK_MAX} 字，lines 写小一点看更近的）\n" + text[-LOOK_MAX:]
    return f"term:{name}（前台 {pane.get('fg') or '?'}）\n{text or '（屏幕是空的）'}"


def close(name: str) -> str:
    from mods import op

    if not op.bot_is_op():
        raise PermissionError("命令行能操作本机，需要 Bot 自身开着 op 权限")
    name = _name((KIND, name))
    with _lock:
        known = _live.pop(name, None) is not None
        _state().pop(name, None)
        result = _tmux("kill-session", "-t", f"={name}", check=False)
    return f"term:{name} 关掉了" if known or not result.returncode else f"term:{name} 本来就没开"


def listing() -> str:
    with _lock:
        panes = _panes() if _live else {}
        rows = [f"term:{name}（前台 {(panes or {}).get(name, {}).get('fg') or '?'}）" for name in sorted(_live)]
    return "开着的命令行窗口：" + "、".join(rows) if rows else "没有开着的命令行窗口"


def _resume() -> None:
    """Pick up sessions that outlived the last process, from the offset already pushed."""
    if not os.path.exists(_socket()) or not available():
        return
    _prepare_root()
    panes = _panes()
    if panes is None:
        return
    state = _state()
    with _lock:
        for name in list(state):
            if name not in panes or not _NAME.fullmatch(name):
                state.pop(name, None)
        for name in panes:
            if not _NAME.fullmatch(name):
                continue
            path = _logpath(name)
            stored = state.setdefault(name, {"offset": os.path.getsize(path) if os.path.exists(path) else 0,
                                             "pending": None})
            _tmux("pipe-pane", "-o", "-t", _target(name), "cat >> " + shlex.quote(path), check=False)
            _watch(name, int(stored.get("offset", 0)))


def on_load(_ctx) -> None:
    from mods import chat, is_available

    if not is_available("chat"):
        raise RuntimeError("命令行窗口需要 chat 模块加载成功")
    # WHY: 攒批在这里做而不是交给 attention 的防抖：一次 pty 读一条记录会把窗口刷爆，一批就是
    # 一条记录，到了才登记，所以种类上不再声明 debounce。整段粘贴，不按空行拆。
    chat.kind(KIND, send, whole=True, follow=True, wait_text=True)
    try:
        _resume()
    except Exception:
        _log.exception("接回上次的命令行窗口失败")


def on_exit() -> None:
    # WHY: 会话不随 Bot 退出：长期命令行就是要跨重启活着，重启后从记下的位置接着读。
    # 维护者要看，`tmux -S data/term/tmux.sock attach -t main`。
    _stopping.set()
    if _reader is not None:
        _reader.join(timeout=2)
