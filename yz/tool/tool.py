from threading import Thread
import asyncio
import time
import os

def mkdirs(path):
    if not os.path.exists(path):
        os.makedirs(path)

def to_thread(func):
    '''
    此装饰器不打算获取返回值
    返回函数的线程
    '''
    def wrapper(*args, **kargs):
        thread = Thread(target=func, args=args, kwargs=kargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper


def merge_dic(dic0, dic1):
    new_dic = {}
    new_dic.update(dic0)
    new_dic.update(dic1)
    return new_dic

# copy的
import inspect
import ctypes
def _async_raise(tid, exctype, endfunc=lambda:None):
    """raises the exception, performs cleanup if needed"""
    tid = ctypes.c_long(tid)
    if not inspect.isclass(exctype):
        exctype = type(exctype)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
        raise SystemError("PyThreadState_SetAsyncExc failed")
    endfunc()
def stop_thread(thread, exctype=SystemExit, endfunc=lambda:None):
    _async_raise(thread.ident, exctype, endfunc)

@to_thread
def delay(sec, func, *args, **kargs):
    time.sleep(sec)
    func(*args, **kargs)


class dicts:
    @staticmethod
    def get(d,*k_):
        '''递归打开字典'''
        if  len(k_)==1:
            return d[k_[0]]
        else:
            return dicts.get(d[k_[0]],*k_[1:])

    @staticmethod
    def set(d, value, *k_):
        '''递归写入字典'''
        if  len(k_)==1:
            d[k_[0]] = value
        else:
            if k_[0] not in d.keys():
                d[k_[0]]={}
            dicts.set(d[k_[0]], value, *k_[1:])

    @staticmethod
    def update(d1, d2):
        '''递归更新字典
        参考:https://www.coder.work/article/1283998
        '''
        return {
            **d1, **d2,
            **{k: dicts.update(d1[k], d2[k]) for k in {*d1} & {*d2} if isinstance(d1[k],dict)}
        }

def cutstart(s:str, start:str):
    return s[len(start):]

def arun(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as e:
        return e.value

import re
 
def validateTitle(title):
    rstr = r"[\/\\\:\*\?\"\<\>\|]"  # '/ \ : * ? " < > |'
    new_title = re.sub(rstr, "_", title).strip()  # 替换为下划线
    return new_title