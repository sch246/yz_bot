"""The process-wide logger configuration, and the streams the terminal shows.

Two independent axes meet here.  *Severity* decides whether something is an
incident: it selects the level and is what ``app.log`` is a record of.  *Stream*
decides which subsystem a record belongs to: it selects the logger name and is
what the terminal subscribes to.  Neither substitutes for the other, and mixing
them is what made every producer invent its own ``print``.

See docs/working/proposals/log-streams.md.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
import os
import re
import sys
import threading
import time

from termcolor import colored

from mods import INFRA
from mods.command import command


PHASE = INFRA
# The reverse order keeps logging alive while the listener closes and storage
# performs its final save.
LOAD_BEFORE = ("connect", "history", "storage")

# Every write renews the holder's lease on the terminal.  A slow but still
# typing producer keeps renewing and keeps its line; a stuck one lets the lease
# expire and stops blocking everyone else.
LINE_LEASE = 5.0

# Streams share one root so that a subscription prefix can never accidentally
# select a severity-axis module logger, and so ``.log`` can tell the two apart.
STREAM_ROOT = "yz"
# Everything that used to print straight to stdout now belongs to some stream
# under the root, so subscribing the root alone reproduces the terminal as it
# was.  Narrowing is then a deliberate act rather than a later discovery.
DEFAULT_SUBSCRIPTIONS = (STREAM_ROOT,)

# One rotating file per top-level stream, so ``tail -f log/llm.log`` in a tmux
# window is a stream and nothing else.  Sub-streams share their parent's file:
# they exist to be subscribed separately, not to be tailed separately, and the
# full logger name is on every line for grepping.
LOG_ROOT = "log"
BACKUP_DAYS = 7

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STREAM_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]*")

_handler: TimedRotatingFileHandler | None = None
_stream_handler: _StreamFileHandler | None = None
_console_handler: logging.StreamHandler | None = None
_stdout: _LineAtomicStream | None = None
_lock = threading.Lock()
# Held for the length of one terminal write, by both the wrapped stdout and the
# console log handler, so the two can never split each other's output.
_output_lock = threading.RLock()
# Logger names seen since start.  Producers need no registration: appearing once
# is what makes a stream listable, so ``.log`` can show one nobody subscribed.
_seen: set[str] = set()


def stream(name: str) -> logging.Logger:
    """Return the logger for one output stream, e.g. ``stream("llm.request")``.

    The name is an exit, not a severity: it says which stream a record belongs
    to, so the terminal can drop some of them and a future consumer can follow
    one.  Choosing a name is the whole cost of adding a producer.
    """
    return logging.getLogger(f"{STREAM_ROOT}.{name}")


def _stored_entries() -> list[str] | None:
    """The live subscription list from storage, or ``None`` when unavailable."""
    from mods import get_available

    storage = get_available("storage")
    if storage is None:
        return None
    try:
        return storage.get("log", "terminal", lambda: list(DEFAULT_SUBSCRIPTIONS))
    except Exception:
        # A terminal that cannot read its subscription still has to print.
        return None


def entries() -> list[str]:
    stored = _stored_entries()
    return list(DEFAULT_SUBSCRIPTIONS) if stored is None else stored


def _bare(entry: str) -> str:
    return entry[1:] if entry.startswith("-") else entry


def subscribed(name: str) -> bool:
    """Whether the terminal shows *name* at INFO; the longest prefix decides.

    ``yz`` subscribes everything and ``-yz.storage`` then carves one stream back
    out, so narrowing a broad subscription does not mean listing what remains.
    """
    best, result = -1, False
    for entry in entries():
        prefix = _bare(entry)
        if name != prefix and not name.startswith(prefix + "."):
            continue
        if len(prefix) > best:
            best, result = len(prefix), not entry.startswith("-")
    return result


class _LineAtomicStream:
    """Let one thread type its line live while everyone else waits its end.

    Several threads share this terminal: the LLM stream writes deltas with no
    newline, the listener writes a received-message prefix before chatlog
    appends the body, and subtask workers report progress.  Writing straight
    through lets an arriving message splice itself into the middle of a model's
    sentence.

    So the thread holding an unfinished line owns the terminal and keeps writing
    through it -- the character-by-character effect is the point and survives
    intact -- while other threads' finished lines queue up and go out the moment
    that line ends.  Deferring the interrupters rather than buffering the writer
    is what keeps live typing; the cost is that background lines can appear a
    little late, and after the reply they arrived during.

    Holding is a lease that every write renews for ``LINE_LEASE``: a slow but
    still typing holder keeps its line, while a stuck one lets the lease run out
    and gives way to whatever is waiting.  A holder that died mid-line gives way
    immediately.  Both are noticed on the next write by another thread, which is
    the first moment anyone is waiting to be seen.  The holder is tracked by
    thread object rather than by ``get_ident()``, whose values are recycled once
    a thread exits -- a new thread inheriting the number would otherwise be
    taken for the old one and write into its abandoned line.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self._owner: threading.Thread | None = None
        self._lease_until = 0.0                          # renewed by each write
        self._tail = ""                                  # the holder's unfinished line
        self._pending: dict[threading.Thread, str] = {}  # other threads' unfinished lines
        self._queue: list[str] = []                      # finished lines awaiting the holder

    def write(self, value: str) -> int:
        if not value:
            return 0
        with _output_lock:
            thread = threading.current_thread()
            if self._owner is None or self._owner is thread:
                self._write_through(thread, value)
            else:
                self._hold(thread, value)
                self._expire()
        return len(value)

    def _write_through(self, thread: threading.Thread, value: str) -> None:
        # A thread that started a line while someone else held the terminal
        # rejoins it here: whatever it buffered belongs in front of what it is
        # writing now.  Without this the line goes out beheaded and its start
        # only surfaces at ``drain``, after everything it preceded.
        value = self._pending.pop(thread, "") + value
        self._stream.write(value)
        self._stream.flush()
        self._tail = (self._tail + value).rpartition("\n")[2]
        if self._tail:
            self._owner = thread
            self._lease_until = time.monotonic() + LINE_LEASE
        else:
            self._owner = None
            self._release()

    def _hold(self, thread: threading.Thread, value: str) -> None:
        head, separator, tail = (self._pending.get(thread, "") + value).rpartition("\n")
        if separator:
            self._queue.append(head + separator)
        if tail:
            self._pending[thread] = tail
        else:
            self._pending.pop(thread, None)

    def _expire(self) -> None:
        """End the held line when its holder is gone, or stuck with output waiting."""
        alive = self._owner.is_alive()
        if alive and (time.monotonic() < self._lease_until or not self._queue):
            return
        self._cut()

    def _cut(self) -> None:
        if self._tail:
            self._stream.write("\n")
            self._tail = ""
        self._owner = None
        self._release()

    def _release(self) -> None:
        if not self._queue:
            return
        self._stream.write("".join(self._queue))
        self._stream.flush()
        self._queue.clear()

    def flush(self) -> None:
        with _output_lock:
            self._stream.flush()

    def drain(self) -> None:
        """Finish the held line and send everything still waiting."""
        with _output_lock:
            for thread in list(self._pending):
                self._queue.append(self._pending.pop(thread) + "\n")
            self._cut()
            self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _is_stream(name: str) -> bool:
    return name == STREAM_ROOT or name.startswith(STREAM_ROOT + ".")


def _stream_file(name: str) -> str | None:
    """The file a stream record belongs in: ``yz.llm.request`` -> ``llm``."""
    if not _is_stream(name):
        return None
    parts = name.split(".")
    return parts[1] if len(parts) > 1 else STREAM_ROOT


class _AttributionFilter(logging.Filter):
    """Tag every record with the interaction its thread is currently serving.

    The identity is read from ``context``, so existing and future log calls
    carry it without one call site passing an argument.  That is the whole
    reason this is a filter and not a parameter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.interaction = _interaction()
        return True


def _interaction() -> str:
    from mods import context

    try:
        event = context.current()
        if event is None or event.get("user_id") is None:
            return ""
        group_id, user_id = context.interaction_key(event)
    except Exception:
        # Attribution is a convenience; never let it drop a record.
        return ""
    return f" [u{user_id}]" if group_id is None else f" [g{group_id} u{user_id}]"


class _StreamFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return _is_stream(record.name)


class _AppFilter(logging.Filter):
    """app.log stays the severity record now that streams have their own files."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or not _is_stream(record.name)


class _StreamFileHandler(logging.Handler):
    """Fan stream records out to one rotating file each, opened on first use.

    Producers register nothing: a stream gets a file the first time it says
    anything, the same way it becomes listable in ``.log``.
    """

    def __init__(self) -> None:
        super().__init__()
        self._files: dict[str, TimedRotatingFileHandler] = {}

    def emit(self, record: logging.LogRecord) -> None:
        name = _stream_file(record.name)
        if name is None:
            return
        try:
            self._for(name).emit(record)
        except Exception:
            self.handleError(record)

    def _for(self, name: str) -> TimedRotatingFileHandler:
        # ``logging`` holds this handler's lock across emit, so the map needs
        # no lock of its own.
        handler = self._files.get(name)
        if handler is None:
            os.makedirs(LOG_ROOT, exist_ok=True)
            handler = TimedRotatingFileHandler(
                os.path.join(LOG_ROOT, name + ".log"),
                when="midnight",
                interval=1,
                backupCount=BACKUP_DAYS,
                encoding="utf-8",
            )
            handler.setFormatter(self.formatter)
            self._files[name] = handler
        return handler

    def flush(self) -> None:
        for handler in self._files.values():
            handler.flush()

    def close(self) -> None:
        while self._files:
            _, handler = self._files.popitem()
            handler.close()
        super().close()


class _FileFormatter(logging.Formatter):
    """Keep the file plain: producers colour for the terminal, not for evidence."""

    def format(self, record: logging.LogRecord) -> str:
        record.interaction = getattr(record, "interaction", "")
        return _ANSI.sub("", super().format(record))


class _ConsoleFormatter(logging.Formatter):
    COLORS = {
        logging.WARNING: "light_yellow",
        logging.ERROR: "light_red",
        logging.CRITICAL: "light_red",
    }

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno < logging.WARNING:
            # A stream record keeps whatever shape its producer chose: these
            # were plain prints and must still read as one line of output.
            return record.getMessage()
        # The rotating file keeps full tracebacks.  The live terminal is a
        # status surface, so one failing periodic network call stays one line.
        exc_info, exc_text = record.exc_info, record.exc_text
        record.exc_info = None
        record.exc_text = None
        try:
            value = super().format(record)
        finally:
            record.exc_info = exc_info
            record.exc_text = exc_text
        return colored(value, self.COLORS.get(record.levelno, "light_cyan"))


class _ConsoleFilter(logging.Filter):
    """Subscribed streams from INFO up, plus WARNING and up from everywhere.

    The floor is not subscribable: unsubscribing controls INFO traffic, and must
    never be able to silence a failure.  Nothing is lost either way -- app.log
    keeps the record whether or not the terminal showed it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        _seen.add(record.name)
        return record.levelno >= logging.WARNING or subscribed(record.name)


def _listing() -> str:
    lines = ["终端订阅：" + (" ".join(entries()) or "（空）")]
    lines.extend(
        f'  {"✓" if subscribed(name) else "×"} {name}' for name in sorted(_seen)
    )
    if len(lines) == 1:
        lines.append("  （启动以来还没有流产出过记录）")
    return "\n".join(lines)


@command
def run(body: str) -> str | None:
    """管理终端订阅的日志流（管理员）。

    .log                列出启动以来出现过的流，以及当前订阅集
    .log on <前缀>…     订阅，按 logger 名前缀匹配（yz.llm 含 yz.llm.request）
    .log off <前缀>…    退订，可以从更宽的订阅里挖掉一条
    .log only <前缀>…   整体替换订阅集，不给前缀表示清空

    WARNING 以上不受订阅集影响，始终进终端；退订也不影响 app.log 的记录。
    """
    from mods import op

    if not op.require_op():
        return None
    parts = body.split()
    if not parts:
        return _listing()
    action, names = parts[0], parts[1:]
    if action not in ("on", "off", "only"):
        return run.__doc__
    if not names and action != "only":
        return run.__doc__
    # ``only`` writes the set literally, so it is the one verb that may spell a
    # negated entry; ``on`` and ``off`` carry the polarity in the verb itself.
    invalid = [
        name
        for name in names
        if not _STREAM_NAME.fullmatch(_bare(name) if action == "only" else name)
    ]
    if invalid:
        return "流名无效：" + " ".join(invalid)
    stored = _stored_entries()
    if stored is None:
        return "storage 不可用，订阅集暂时无法修改"
    if action == "only":
        stored[:] = names
    else:
        kept = [entry for entry in stored if _bare(entry) not in names]
        stored[:] = kept + [name if action == "on" else f"-{name}" for name in names]
    return _listing()


def on_load(ctx) -> None:
    global _handler, _stream_handler, _console_handler, _stdout
    with _lock:
        if _handler is not None:
            return
        if _stdout is None:
            _stdout = _LineAtomicStream(sys.stdout)
            sys.stdout = _stdout
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        attribution = _AttributionFilter()
        formatter = _FileFormatter(
            "%(asctime)s - %(name)s - %(levelname)s -%(interaction)s %(message)s"
        )
        handler = TimedRotatingFileHandler(
            "app.log",
            when="midnight",
            interval=1,
            backupCount=BACKUP_DAYS,
            encoding="utf-8",
        )
        handler.addFilter(attribution)
        handler.addFilter(_AppFilter())
        handler.setFormatter(formatter)
        root.addHandler(handler)
        _handler = handler
        stream_handler = _StreamFileHandler()
        stream_handler.addFilter(attribution)
        stream_handler.addFilter(_StreamFilter())
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
        _stream_handler = stream_handler
        # The console handler writes through the wrapped stdout rather than to
        # stderr, so a warning obeys the same line ownership as everything else
        # instead of splicing itself into a reply being typed.
        console_handler = logging.StreamHandler(_stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.addFilter(_ConsoleFilter())
        console_handler.setFormatter(
            _ConsoleFormatter("[%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(console_handler)
        _console_handler = console_handler


def on_exit() -> None:
    global _handler, _stream_handler, _console_handler, _stdout
    with _lock:
        handler, _handler = _handler, None
        stream_handler, _stream_handler = _stream_handler, None
        console_handler, _console_handler = _console_handler, None
        stream, _stdout = _stdout, None
    if stream is not None:
        stream.drain()
        if sys.stdout is stream:
            sys.stdout = stream._stream
    if console_handler is not None:
        logging.getLogger().removeHandler(console_handler)
        console_handler.close()
    root = logging.getLogger()
    for closing in (stream_handler, handler):
        if closing is None:
            continue
        root.removeHandler(closing)
        closing.flush()
        closing.close()
