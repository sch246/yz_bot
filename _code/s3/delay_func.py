'''修饰一个函数，函数调用之间保持时间间隔'''

from time import sleep
from typing import Callable
from queue import Queue, Empty
from functools import wraps
import threading

from s3.thread import to_thread, SimpleFuture

class _QueueExecutor:
    def __init__(
            self,
            delay_secs:float|int|Callable[..., float|int]=0,
            max_queue_size:int=0
        ):
        self._work_queue = Queue(max_queue_size)
        self._delay_secs = delay_secs
        self._stop_event = threading.Event()
        self.loop()

    @to_thread
    def loop(self):
        while not self._stop_event.is_set():
            try:
                future, func, args, kws = self._work_queue.get(timeout=1.0)  # 添加超时，避免永久阻塞
                
                delay = (self._delay_secs(*args,**kws)
                         if callable(self._delay_secs)
                         else self._delay_secs)

                if delay >= 0:
                    sleep(delay)

                # 使用带超时的函数执行
                result = self._execute_with_timeout(func, args, kws, timeout=10.0)  # 10秒超时
                
                if isinstance(result, Exception):
                    future.set_exception(result)
                else:
                    future.set_result(result)

                if delay < 0:
                    sleep(-delay)
                    
            except Empty:
                # 这是正常的超时，继续循环
                continue
            except Exception as e:
                # 记录错误但继续运行
                print(f"队列执行器错误: {e}")
                continue

    def _execute_with_timeout(self, func, args, kws, timeout=10.0):
        """带超时执行函数"""
        result = None
        exception = None
        
        def worker():
            nonlocal result, exception
            try:
                result = func(*args, **kws)
            except Exception as e:
                exception = e
        
        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        thread.join(timeout)
        
        if thread.is_alive():
            # 函数执行超时
            return TimeoutError(f"函数执行超时 ({timeout}秒)")
        elif exception is not None:
            # 函数执行出错
            return exception
        else:
            # 函数正常执行完成
            return result

    def submit(self, func, args, kws):
        future = SimpleFuture()
        if self._work_queue.full():
            print('队列已满,函数被忽略:', (func, args, kws))
            future.set_exception(RuntimeError("队列已满"))
        else:
            self._work_queue.put((future, func, args, kws))
        return future
    
    def shutdown(self):
        """停止队列执行器"""
        self._stop_event.set()


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
