'''修饰一个函数，函数调用之间保持时间间隔'''

from threading import Event
from time import sleep
from typing import Callable
from queue import Queue
from functools import wraps

from s3.thread import to_thread

class SimpleFuture:
    def __init__(self) -> None:
        self._event = Event()
        self._result = None

    def set_result(self, value):
        self._result = value
        self._event.set()

    def result(self, timeout:float|None = None):
        self._event.wait(timeout)
        return self._result

    def done(self):
        return self._event.is_set()

class _QueueExecutor:
    def __init__(
            self,
            delay_secs:float|int|Callable[..., float|int]=0,
            max_queue_size:int=0
        ):
        self._work_queue = Queue(max_queue_size)
        self._delay_secs = delay_secs
        self.loop()

    @to_thread
    def loop(self):
        while True:

            future, func, args, kws = self._work_queue.get()

            delay = (self._delay_secs(*args,**kws)
                     if callable(self._delay_secs)
                     else self._delay_secs)

            if delay>=0:
                sleep(delay)

            future.set_result(func(*args, **kws))

            if delay<0:
                sleep(-delay)

    def submit(self, func, args, kws):
        future = SimpleFuture()
        if self._work_queue.full():
            print('队列已满,函数被忽略:', (func, args, kws))
            future.set_result(None)
        else:
            self._work_queue.put((future, func, args, kws))
        return future


def call_delay(
        delay_secs:float|int|Callable[..., float|int],
        max_size:int=0
    ):
    '''
    secs: 函数调用之间保持的最短时间间隔，单位是秒，正数表示前摇，负数表示后摇，否则将函数调用放进队列，如果是函数，则可以根据参数计算时间间隔

    max_size: 当达到这个队列长度时，多余的调用会被忽视，<=0表示不限制

    返回一个装饰器，其装饰的函数被调用时返回一个对象，可以选择调用.result()来等待结果
    '''
    def wrapper(func:Callable):
        executor = _QueueExecutor(delay_secs, max_size)

        @wraps(func)
        def call(*args, **kws):
            return executor.submit(func, args, kws)

        return call

    return wrapper
