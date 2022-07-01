from functools import reduce
from threading import Thread
import asyncio
import time
import os

from yz.tool.data import cq_load_dic,cq_trans_dic,re_need_trans


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


def sprint(text):
    re.compile('(<[rgbRGB]{0,3}>)([\S\s]*?)(<\/>)')
    def w(s):
        rgb = s[1:-1]
    re.sub('<[rgbRGB]{0,3}>')


def fmt(color=None, bg=None,type=None):
    '''type可以有 
    c(close关闭), 
    b(bold高亮), 
    u(underline下划线), 
    f(flash闪烁), 
    r(reverse反显),
    m(miss消隐),
    '''
    if color==None and bg==None and type==None:
        return "\033[0m"
    def get_rgb(c):
        c = c.lower()
        return ('r' in c) + 2*('g' in c) + 4*('b' in c)
    i = []
    if isinstance(color,str):
        i.append(str(30 + get_rgb(color)))
    if isinstance(bg,str):
        i.append(str(40 + get_rgb(bg)))
    if isinstance(type,str):
        d = dict(zip('cbufrm','014578'))
        i.append(d[type])
    return f"\033[{';'.join(i)}m"
def fms(s,color=None, bg=None,type=None):
    return fmt(color,bg,type) + s + fmt()

re_CQdatas = '(?:,[^,=]+=[^,\]]+)*'

_re_CQ = re.compile(f'\[CQ:[^,\]]+{re_CQdatas}\]')
re_CQ = re.compile(f'\[CQ:(?P<type>[^,\]]+)(?P<data>{re_CQdatas})\]')

def find_all_CQ(s:str):
    return _re_CQ.findall(s)

def load_CQ(CQ:str):
    '''将字符串形式的单个CQ转化为字典，并且将其中乱七八糟的东东转化为正常'''
    CQ = re.sub('\s','',CQ)  # 去掉空白符
    mt = re_CQ.match(CQ)
    stype=mt.group('type')
    sdata=mt.group('data')
    if sdata:
        # 若CQ有参数,分割并获取参数字符串,再次分割并转化为字典
        str_list = sdata[1:].split(',')
        def f(s:str):
            s = load_cq(s)
            i = s.index('=')
            return s[:i], s[i+1:]
        data = dict(map(f ,str_list))
    else:
        data={}
    return {'type':stype,'data':data}

def make_CQ(d:dict):
    '''将字典形式的CQ转化为字符串形式，并且将对应的字符转换为CQ的乱七八糟的东东'''
    type=d['type']
    data = ''.join(map(lambda x:','+trans_to_cq(f'{x[0]}={x[1]}'), d['data'].items()))
    return f'[CQ:{type}{data}]'

def trans_rep(src_rep:str):
    src_ = re.compile('{(\w+?)}')
    keys = set()
    def f(match:re.Match):
        key = match.group(1)
        if key in keys:
            rtn = f'(?P={key})'
        else:
            if key[0].isupper():
                rtn= f'(?P<{key}>[\S\s]+)'
            else:
                rtn= f'(?P<{key}>\S+)'
        keys.add(key)
        return rtn
    # 得检测重复的group并替换成引用
    return src_.sub(f,load_cq(src_rep))

def rep_str(rep:str, tar:str, src:str):
    re_rep = re.compile(trans_rep(rep))
    match = re_rep.match(src)
    if not match:
        return False
    else:
        for key, value in match.groupdict().items():
            if value == None:
                value = ''
            tar = tar.replace(f'{{{key}}}',value)
        return tar

def set_rep(rep:str, tar:str):
    return lambda src:rep_str(rep,tar,src)

def trans_to_cq(s:str):
    '''将正常的符号转码为符合CQ转码的东东'''
    for k,v in cq_trans_dic.items():
        s = s.replace(k,v)
    return s
def load_cq(s:str):
    '''将接收的乱七八糟的转码为正常的东东'''
    for k,v in cq_load_dic.items():
        s = s.replace(k,v)
    return s

def cut_head(s:str,*h):
    for h_ in h:
        if s.startswith(h_):
            return s[len(h_):]
    return s

def tabbed(s:str):
    return all(map(lambda x: x.startswith('    ') or x.startswith('\t'),s.splitlines()))

def cut_tab(s):
    s = cut_head(s,'    ','\t')
    s = s.replace('\n    \t','\n    \t\t')
    s = s.replace('\n    ','\n')
    s = s.replace('\n\t','\n')
    return s

def add_tab(s):
    s = '    '+s
    s = s.replace('\n','\n    ')
    return s

def isint(s:str):
    if s.startswith('+') or s.startswith('-'):
        s = s[1:]
    return s.isdigit()

def isfunc(o:object):
    return hasattr(o,'__call__')

def hasfunc(o:object,s:str):
    if isinstance(s,str) and hasattr(o,s):
        return hasattr(getattr(o,s),'__call__')
    return False

def getlines(s:str, start=None, end=None):
    return '\n'.join(s.splitlines()[start:end])

def re_mark(s:str):
    '''返回精准匹配s的正则表达式'''
    return reduce(lambda x, y: x.replace(y,'\\'+y),re_need_trans,s)

def insert_str(s:str,insert:str,start:int,end:int=None):
    if end==None:
        return s[:start] + insert +s[start:]
    return s[:start] + insert +s[end:]

class Now:
    dic={}
    def __getitem__(self,index):
        return self.dic[index]