"""Small thread helpers which preserve the current chat destination."""

from __future__ import annotations

from functools import wraps
import logging
import threading
from typing import Callable, Generic, TypeVar

from mods import context


T = TypeVar("T")
_log = logging.getLogger(__name__)


class SimpleFuture(Generic[T]):
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._result: T | None = None
        self._exception: BaseException | None = None
        self._callbacks: list[Callable[[SimpleFuture[T]], object]] = []

    def set_result(self, value: T) -> None:
        self._finish(value=value)

    def set_exception(self, error: BaseException) -> None:
        self._finish(error=error)

    def _finish(self, value: T | None = None, error: BaseException | None = None) -> None:
        with self._lock:
            if self._event.is_set():
                raise RuntimeError("future already completed")
            self._result = value
            self._exception = error
            callbacks = list(self._callbacks)
            self._callbacks.clear()
            self._event.set()
        for callback in callbacks:
            try:
                callback(self)
            except Exception:
                _log.exception("future completion callback failed")

    def result(self, timeout: float | None = None) -> T:
        if not self._event.wait(timeout):
            raise TimeoutError("result not ready in time")
        if self._exception is not None:
            raise self._exception
        return self._result  # type: ignore[return-value]

    def exception(self, timeout: float | None = None) -> BaseException | None:
        if not self._event.wait(timeout):
            raise TimeoutError("result not ready in time")
        return self._exception

    def done(self) -> bool:
        return self._event.is_set()

    def add_done_callback(self, callback: Callable[[SimpleFuture[T]], object]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback(self)


def to_thread(ret: str | None | Callable = "future"):
    """Run a function in a daemon thread.

    ``ret`` may be ``"future"`` (default), ``"thread"`` or ``None``.  Used
    bare as ``@to_thread``, it keeps the default future return.
    """
    if callable(ret):
        function = ret
        return to_thread("future")(function)
    if ret not in ("future", "thread", None):
        raise ValueError("ret must be 'future', 'thread', or None")

    def decorate(function: Callable):
        @wraps(function)
        def call(*args, **kwargs):
            future: SimpleFuture = SimpleFuture()
            origin = context.current()

            def run() -> None:
                context.set_current(origin)
                try:
                    future.set_result(function(*args, **kwargs))
                except BaseException as error:
                    future.set_exception(error)
                finally:
                    context.clear_current()

            worker = threading.Thread(
                target=run,
                name=f"{function.__module__}.{function.__name__}",
                daemon=True,
            )
            worker.start()
            if ret == "thread":
                return worker
            if ret == "future":
                return future
            return None

        return call

    return decorate


def ctrlc_decorator(on_exit: Callable[[], object] = lambda: None):
    """Run a cleanup callback when a blocking call receives Ctrl+C."""
    def decorate(function: Callable):
        threaded = to_thread("future")(function)

        @wraps(function)
        def call(*args, **kwargs):
            future = threaded(*args, **kwargs)
            try:
                return future.result()
            except KeyboardInterrupt:
                on_exit()
                raise

        return call

    return decorate
