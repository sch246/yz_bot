'''简单创建个多线程'''

from threading import Thread


def to_thread(func):
    '''
    此装饰器不打算获取返回值
    '''
    def wrapper(*args, **kargs):
        thread = Thread(target=func, args=args, kwargs=kargs)
        thread.daemon = True
        thread.start()
    return wrapper