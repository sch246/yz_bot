"""The process-wide application logger configuration."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
import threading

from termcolor import colored

from mods import INFRA


PHASE = INFRA
# The reverse order keeps logging alive while the listener closes and storage
# performs its final save.
LOAD_BEFORE = ("connect", "history", "storage")

_handler: TimedRotatingFileHandler | None = None
_console_handler: logging.StreamHandler | None = None
_lock = threading.Lock()


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


class _ConsoleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Storage already emits its own concise terminal report.
        return record.name != "mods.storage"


def on_load(ctx) -> None:
    global _handler, _console_handler
    with _lock:
        if _handler is not None:
            return
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
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.addFilter(_ConsoleFilter())
        console_handler.setFormatter(
            _ConsoleFormatter("[%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(console_handler)
        _console_handler = console_handler


def on_exit() -> None:
    global _handler, _console_handler
    with _lock:
        handler, _handler = _handler, None
        console_handler, _console_handler = _console_handler, None
    if console_handler is not None:
        logging.getLogger().removeHandler(console_handler)
        console_handler.close()
    if handler is None:
        return
    logging.getLogger().removeHandler(handler)
    handler.flush()
    handler.close()
