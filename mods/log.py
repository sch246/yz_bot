"""The process-wide application logger configuration."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
import sys
import threading
import time

from termcolor import colored

from mods import INFRA


PHASE = INFRA
# The reverse order keeps logging alive while the listener closes and storage
# performs its final save.
LOAD_BEFORE = ("connect", "history", "storage")

# Every write renews the holder's lease on the terminal.  A slow but still
# typing producer keeps renewing and keeps its line; a stuck one lets the lease
# expire and stops blocking everyone else.
LINE_LEASE = 5.0

_handler: TimedRotatingFileHandler | None = None
_console_handler: logging.StreamHandler | None = None
_stdout: _LineAtomicStream | None = None
_lock = threading.Lock()
# Held for the length of one terminal write, by both the wrapped stdout and the
# console log handler, so the two can never split each other's output.
_output_lock = threading.RLock()


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


class _ConsoleFormatter(logging.Formatter):
    COLORS = {
        logging.WARNING: "light_yellow",
        logging.ERROR: "light_red",
        logging.CRITICAL: "light_red",
    }

    def format(self, record: logging.LogRecord) -> str:
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


class _LockedStreamHandler(logging.StreamHandler):
    """Take the shared terminal lock so a warning cannot land mid-line."""

    def emit(self, record: logging.LogRecord) -> None:
        with _output_lock:
            super().emit(record)


class _ConsoleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Storage already emits its own concise terminal report.
        return record.name != "mods.storage"


def on_load(ctx) -> None:
    global _handler, _console_handler, _stdout
    with _lock:
        if _handler is not None:
            return
        if _stdout is None:
            _stdout = _LineAtomicStream(sys.stdout)
            sys.stdout = _stdout
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        handler = TimedRotatingFileHandler(
            "app.log",
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        root.addHandler(handler)
        _handler = handler
        console_handler = _LockedStreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.addFilter(_ConsoleFilter())
        console_handler.setFormatter(
            _ConsoleFormatter("[%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(console_handler)
        _console_handler = console_handler


def on_exit() -> None:
    global _handler, _console_handler, _stdout
    with _lock:
        handler, _handler = _handler, None
        console_handler, _console_handler = _console_handler, None
        stream, _stdout = _stdout, None
    if stream is not None:
        stream.drain()
        if sys.stdout is stream:
            sys.stdout = stream._stream
    if console_handler is not None:
        logging.getLogger().removeHandler(console_handler)
        console_handler.close()
    if handler is None:
        return
    logging.getLogger().removeHandler(handler)
    handler.flush()
    handler.close()
