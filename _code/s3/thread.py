'''简单创建个多线程'''

from concurrent.futures import Future
from threading import Thread
from functools import wraps

def to_thread(func, ret_thread=False):
    '''
    此装饰器不打算获取返回值
    '''
    @wraps(func)
    def wrapper(*args, **kargs):
        thread = Thread(target=func, args=args, kwargs=kargs)
        thread.daemon = True
        thread.start()
        if ret_thread:
            return thread
    return wrapper


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
