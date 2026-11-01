"""CLI agent windows: aliases in sessions.json, one background turn per window."""

import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from . import _process, agy, codex, dsh


LOAD_AFTER = ("chat", "op")
SESSIONS = Path("data/agents/sessions.json")
TIMEOUT = 1800
DRIVERS = {"dsh": dsh, "agy": agy, "codex": codex}
_lock = threading.RLock()
_busy: set[tuple] = set()
_workers: set[threading.Thread] = set()
_stopping = False
_log = logging.getLogger(__name__)


def _read():
    try:
        sessions = json.loads(SESSIONS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as error:
        raise ValueError("agents/sessions.json 不是有效 JSON，请修好后再发送") from error
    if not isinstance(sessions, dict):
        raise ValueError("agents/sessions.json 顶层必须是别名到配置的对象")
    return sessions


def _write(sessions):
    SESSIONS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SESSIONS.with_name(SESSIONS.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(sessions, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, SESSIONS)
    finally:
        temporary.unlink(missing_ok=True)


def _session(window):
    kind, alias = window
    if kind not in DRIVERS or not isinstance(alias, str) or not re.fullmatch(r"[\w.-]+", alias) or alias in (".", ".."):
        raise ValueError("CLI 窗口写 dsh:main、agy:review 或 codex:code；别名只用字母、数字、中文、点、横线、下划线")
    sessions = _read()
    entry = sessions.get(alias, {})
    if not isinstance(entry, dict):
        raise ValueError(f"{alias} 的会话配置必须是 JSON 对象")
    if entry.get("kind", kind) != kind or entry.get("target", alias) != alias:
        raise ValueError(f"别名 {alias} 已用于另一个窗口，请换一个别名")
    entry = dict(entry)
    now = time.time()
    entry.update(kind=kind, target=alias)
    entry.setdefault("real_id", "")
    entry.setdefault("cwd", "/tmp")
    entry.setdefault("created_at", now)
    entry.setdefault("updated_at", now)
    if not isinstance(entry["real_id"], str):
        raise ValueError(f"{alias} 的 real_id 必须是字符串；新会话填空字符串")
    cwd = Path(entry["cwd"])
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValueError(f"{alias} 的 cwd 必须是已存在的绝对目录")
    timeout = float(entry.get("timeout", TIMEOUT))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("CLI timeout 必须是大于 0 的秒数")
    sessions[alias] = entry
    _write(sessions)
    return entry, timeout


def _finish_session(session, real_id):
    with _lock:
        sessions = _read()
        current = sessions.get(session["target"])
        if not isinstance(current, dict) or any(current.get(key) != session[key]
                                               for key in ("kind", "target", "real_id", "cwd")):
            raise ValueError("运行期间会话配置变了，本轮没有覆盖它；请核对 real_id 后再发送")
        # Reread before writing so parallel windows and edits to model/timeout are retained.
        current.update(real_id=real_id, updated_at=time.time())
        _write(sessions)


def reset_session(window):
    """把一个 CLI 会话的 real_id 置空，下一轮将作为全新会话开始。"""
    kind, alias = window
    with _lock:
        sessions = _read()
        entry = sessions.get(alias)
        if not isinstance(entry, dict):
            raise KeyError(f"未找到会话别名 {alias}")
        old_id = entry.get("real_id", "")
        entry.update(real_id="", updated_at=time.time())
        _write(sessions)
        return old_id


def archive_session(window, archive_alias: str = ""):
    """归档当前会话（复制一份到 archive_alias 并置空当前窗口的 real_id）。"""
    kind, alias = window
    with _lock:
        sessions = _read()
        entry = sessions.get(alias)
        if not isinstance(entry, dict):
            raise KeyError(f"未找到会话别名 {alias}")
        target_name = archive_alias or f"{alias}-archived-{int(time.time())}"
        sessions[target_name] = dict(entry, target=target_name, archived_at=time.time())
        entry.update(real_id="", updated_at=time.time())
        _write(sessions)
        return target_name


def _turn(window, session, prompt, timeout):
    from mods import chat

    text, problem = "", ""
    real_id = session["real_id"]
    try:
        real_id, text, ok, problem = DRIVERS[window[0]].run_turn(session, prompt, timeout)
        if not ok and not problem:
            problem = "CLI 本轮未完成"
        _finish_session(session, real_id)
    except Exception as error:
        problem = f"{problem}\n{type(error).__name__}：{error}".strip()
        if real_id:
            problem += f"\n本轮会话 id：{real_id}"
    body = text or ("（本轮没有文字输出）" if not problem else "")
    if problem:
        body = (body + "\n\n" if body else "") + "运行失败：" + problem
    with _lock:
        try:
            # Keep reply-before-next-prompt order; a woken sender waits for this lock.
            wake = "" if _stopping else f"{window[0]}:{window[1]} 回复了"
            chat.push(window, author=window[0], text=body, wake=wake)
        except Exception:
            _log.exception("CLI 回复写入窗口失败：%s:%s", *window)
        finally:
            _busy.discard(window)
            _workers.discard(threading.current_thread())


def send(window, text):
    """Accept a whole prompt, log it, and immediately return its eight-digit receipt."""
    from mods import chatlog, op

    if not op.bot_is_op():
        raise PermissionError("CLI agent 能操作本机，需要 Bot 自身开着 op 权限")
    window = tuple(window)
    if not str(text).strip():
        raise ValueError("要发给 CLI 的话不能为空")
    with _lock:
        if _stopping:
            raise RuntimeError("Bot 正在退出，请稍后再发")
        if window in _busy:
            raise RuntimeError(f"{window[0]}:{window[1]} 还在处理上一句话，等它回复后再发；这句话留在输入框里")
        session, timeout = _session(window)
        record_id = uuid4().hex[:8]
        chatlog.append_record(*window, "我", text, time.time(), record_id)
        _busy.add(window)
        worker = threading.Thread(target=_turn, args=(window, session, text, timeout),
                                  name=f"cli-{window[0]}-{window[1]}", daemon=True)
        _workers.add(worker)
        try:
            worker.start()
        except Exception as error:
            from mods import chat

            _busy.discard(window)
            _workers.discard(worker)
            # The prompt is already accepted and logged; report launch failure as its reply.
            chat.push(window, author=window[0], text=f"运行失败：无法启动后台线程：{error}",
                      wake=f"{window[0]}:{window[1]} 回复了")
    return record_id



def on_load(_ctx):
    global _stopping
    from mods import chat, is_available

    if not is_available("chat"):
        raise RuntimeError("CLI 窗口需要 chat 模块加载成功")
    _stopping = False
    _process.start()
    for kind in DRIVERS:
        # WHY: CLI 的多段 prompt 是一次任务；按气泡拆开会让第二段撞上忙碌检查，丢掉后半任务。
        chat.kind(kind, send, whole=True)


def on_exit():
    global _stopping
    with _lock:
        _stopping = True
        workers = list(_workers)
    _process.stop()
    for worker in workers:
        worker.join(timeout=5)
