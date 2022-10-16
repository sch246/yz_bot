'''计划任务的增删改查'''

import time
import sched


scheduler = sched.scheduler(time.time)


def _zero_time(now: float) -> int:
    '''输入time.time()的时间，返回今天零点的时间'''
    return int(time.mktime(
        time.strptime(
            time.strftime("%Y-%m-%d", time.localtime(now)),
            "%Y-%m-%d")))


def nextday(*args, **kwargs):
    '''明天运行函数'''
    def wrapper(f):
        scheduler.enterabs(_zero_time(time.time()+86400), 1, f, args, kwargs)
        return f
    return wrapper


def everyday(*args, **kwargs):
    '''每天零点运行函数'''
    def wrapper(f):
        def func():
            nextday()(func)
            f(*args, **kwargs)
        nextday()(func)
        return f
    return wrapper


# def run():
#     while True:  # ? 参考 https://www.coder.work/article/6936775
#         next_ev = scheduler.run(False)
#         if next_ev is not None:
#             time.sleep(min(1, next_ev))
#         else:
#             time.sleep(1)
