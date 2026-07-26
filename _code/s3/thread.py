'''简单创建个多线程'''
from threading import Event
from functools import cache
from typing import Callable

from concurrent.futures import Future
from threading import Thread
from functools import wraps


class SimpleFuture:
    '''
    一个简单的future，用于替代concurrent.futures.Future
    '''
    def __init__(self) -> None:
        self._event = Event()
        self._result = None
        self._exception = None

    def set_result(self, value):
        self._result = value
        self._event.set()

    def set_exception(self, exc):
        self._exception = exc
        self._event.set()

    def result(self, timeout:float|None = None):
        if not self._event.wait(timeout):
            raise TimeoutError("Result not ready in time")
        if self._exception:
            raise self._exception
        return self._result

    def done(self):
        return self._event.is_set()

def to_thread(ret: Callable|str = 'future'):
    '''
    将任意函数转换为线程
    ret: None: 返回None, 'future': 返回future, 'thread': 返回thread
    可以作装饰器使用，默认返回future
    '''
    if callable(ret):
        func = ret
        ret = 'future'
    else:
        func = None

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kargs):
            future = SimpleFuture()
            def run():
                try:
                    future.set_result(func(*args, **kargs))
                except Exception as e:
                    future.set_exception(e)

            thread = Thread(target=run)

            thread.daemon = True
            thread.start()
            if ret == 'thread':
                return thread
            elif ret == 'future':
                return future
            else:
                return None
        return wrapper
    if func:
        return decorator(func)
    return decorator


def ctrlc_decorator(on_exit=lambda:None):
    '''
    让任意函数可以被ctrl+c中断，随后运行回调函数
    '''

    # 这里定义的是装饰器本身
    def decorator(func):

        # 这是被装饰器包裹的函数
        @wraps(func)
        def wrapper(*args, **kwargs):
            res = Future()

            Thread(
                target=lambda:res.set_result(func(*args, **kwargs)),
                daemon=True
            ).start()

            try:
                return res.result()
            except KeyboardInterrupt:
                # 当接收到键盘中断信号时，执行指定的on_exit函数
                on_exit()
                print('bye.')
                exit(0)

        # 返回包裹了原函数的新函数
        return wrapper

    # 返回装饰器
    return decorator
