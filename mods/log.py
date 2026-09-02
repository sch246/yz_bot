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

# A line held this long gives way: waiting output must not be stuck behind one
# slow or hung producer.
HOLD_LIMIT = 5.0

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

    A line held past ``HOLD_LIMIT`` is cut short so nothing waits indefinitely.
    That check runs on each write, which is enough because the owner writes
    continuously; if it stops writing entirely, the next output of any kind
    releases the queue.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self._owner: int | None = None
        self._owner_since = 0.0
        self._tail = ""                       # the owner's unfinished line
        self._pending: dict[int, str] = {}    # other threads' unfinished lines
        self._queue: list[str] = []           # finished lines awaiting the owner

    def write(self, value: str) -> int:
        if not value:
            return 0
        with _output_lock:
            key = threading.get_ident()
            if self._owner is None or self._owner == key:
                self._write_through(key, value)
            else:
                self._hold(key, value)
            self._break_stale_line()
        return len(value)

    def _write_through(self, key: int, value: str) -> None:
        self._stream.write(value)
        self._stream.flush()
        self._tail = (self._tail + value).rpartition("\n")[2]
        if not self._tail:
            self._owner = None
            self._release()
        elif self._owner is None:
            self._owner, self._owner_since = key, time.monotonic()

    def _hold(self, key: int, value: str) -> None:
        head, separator, tail = (self._pending.get(key, "") + value).rpartition("\n")
        if separator:
            self._queue.append(head + separator)
        if tail:
            self._pending[key] = tail
        else:
            self._pending.pop(key, None)

    def _release(self) -> None:
        if not self._queue:
            return
        self._stream.write("".join(self._queue))
        self._stream.flush()
        self._queue.clear()

    def _break_stale_line(self) -> None:
        if not self._queue or self._owner is None:
            return
        if time.monotonic() - self._owner_since < HOLD_LIMIT:
            return
        # End the held line here and let its writer continue on the next one.
        self._stream.write("\n")
        self._tail = ""
        self._owner = None
        self._release()

    def flush(self) -> None:
        with _output_lock:
            self._stream.flush()

    def drain(self) -> None:
        """Finish the held line and send everything still waiting."""
        with _output_lock:
            if self._tail:
                self._stream.write("\n")
                self._tail = ""
            self._owner = None
            for key in sorted(self._pending):
                self._queue.append(self._pending.pop(key) + "\n")
            self._release()
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
