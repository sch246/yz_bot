from threading import Thread
import asyncio

def to_thread(func):
    '''
    此装饰器不打算获取返回值
    返回函数的线程
    '''
    def wrapper(*args, **kargs):
        thread = Thread(target=func, args=args, kwargs=kargs)
        thread.start()
        return thread
    return wrapper

@to_thread
def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()
    print('exit')

def async_to_thread(func):
    '''
    此装饰器装饰异步函数
    返回可等待对象)
    '''
    def wrapper(*args, **kargs):
        # coroutine = asyncio.to_thread(func,*args,**kargs)
        new_loop = asyncio.new_event_loop()                        #在当前线程下创建时间循环，（未启用），在start_loop里面启动它
        start_loop(new_loop)                                       #通过当前线程开启新的线程去启动事件循环
        asyncio.run_coroutine_threadsafe(func(*args,**kargs),new_loop)
    return wrapper

