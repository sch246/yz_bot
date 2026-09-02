"""The process-wide application logger configuration."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
import sys
import threading

from termcolor import colored

from mods import INFRA


PHASE = INFRA
# The reverse order keeps logging alive while the listener closes and storage
# performs its final save.
LOAD_BEFORE = ("connect", "history", "storage")

# One runaway producer must not hold the terminal hostage; past this many
# characters without a newline the partial text goes out as it is.
LINE_CAP = 1 << 16

_handler: TimedRotatingFileHandler | None = None
_console_handler: logging.StreamHandler | None = None
_stdout: _LineAtomicStream | None = None
_lock = threading.Lock()
# Held for the length of one terminal write, by both the wrapped stdout and the
# console log handler, so the two can never split each other's output.
_output_lock = threading.RLock()


class _LineAtomicStream:
    """Hold each thread's partial writes until they form whole lines.

    Several threads share this one terminal: the LLM stream writes deltas with
    no newline, the listener writes a received-message prefix before chatlog
    appends the body, and subtask workers report progress.  Writing straight
    through lets an arriving message splice itself into the middle of a model's
    sentence.  Buffering per thread keeps every producer's line intact without
    any of them knowing about the others, and costs the character-by-character
    typing effect, which is a terminal affordance rather than a record.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self._pending: dict[int, str] = {}

    def write(self, value: str) -> int:
        if not value:
            return 0
        with _output_lock:
            key = threading.get_ident()
            pending = self._pending.get(key, "") + value
            head, separator, tail = pending.rpartition("\n")
            if separator:
                text, pending = head + separator, tail
            elif len(pending) >= LINE_CAP:
                text, pending = pending, ""
            else:
                text = ""
            if pending:
                self._pending[key] = pending
            else:
                self._pending.pop(key, None)
            if text:
                self._stream.write(text)
                self._stream.flush()
        return len(value)

    def flush(self) -> None:
        # A partial line stays pending: flushing it is what this class exists
        # to prevent.  Only the underlying stream is pushed along.
        with _output_lock:
            self._stream.flush()

    def drain(self) -> None:
        """Emit what never reached a newline, so exiting loses nothing."""
        with _output_lock:
            for key in sorted(self._pending):
                text = self._pending.pop(key)
                if text:
                    self._stream.write(text + "\n")
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
