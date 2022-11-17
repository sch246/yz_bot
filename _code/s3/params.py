'''控制函数参数的输入顺序'''

from typing import Any, Callable


def curry(f, _args=None, _kwargs=None):
    '''任意分离参数，使用空括号以调用'''
    if _args is None:
        _args = []
    if _kwargs is None:
        _kwargs = {}

    def _(*args, **kwargs):
        if not args and not kwargs:
            return f(*_args, **_kwargs)
        else:
            return curry(f, [*_args, *args], {**_kwargs, **kwargs})
    return _


def preset(f: Callable, param_func: Callable[[list, dict], tuple[list, dict]]):
    '''覆盖面广但没啥卵用的东东，在函数输入参数后处理参数'''
    def _(*args, **kwargs):
        args, kwargs = param_func(list(args), kwargs)
        return f(*args, **kwargs)
    return _

def arg(f: Callable, action:str, *_args, **_kwargs):
    '''在函数输入参数后调用列表的方法处理顺序参数'''
    def _(*args, **kwargs):
        args = list(args)
        getattr(args, action)(*_args, **_kwargs)
        return f(*args, **kwargs)
    return _

def kwarg(f: Callable, action:str, *_args, **_kwargs):
    '''在函数输入参数后调用字典的方法处理键值参数'''
    def _(*args, **kwargs):
        getattr(kwargs, action)(*_args, **_kwargs)
        return f(*args, **kwargs)
    return _

def append(f: Callable, _args:list):
    '''往后面加参数'''
    def _(*args, **kwargs):
        args = *args, *_args
        return f(*args, **kwargs)
    return _

def insert(f: Callable, i:int, *_args:list):
    '''插入参数'''
    def _(*args, **kwargs):
        args = list(args)
        args[i:i] = _args
        return f(*args, **kwargs)
    return _


def update(f, dic: dict):
    '''更新键值参数'''
    def _(*args, **kwargs):
        kwargs.update(dic)
        return f(*args, **kwargs)
    return _


def setl(l:list, value):
    '''将值塞进列表并且返回这个值'''
    l.append(value)
    return value

if __name__ == "__main__":
    #下面是测试
    def test(a=None, b=None, c=None):
        return [a, b, c]

    print(kwarg(test, 'update', {'c': 99})(1))

    currytest = curry(test)

    f1 = currytest(0, c=99)
    f2 = currytest(2, 8, 1)
    f3 = currytest(333)(c=222)(111)

    print(f1(7777)(), f1(6666)(), f2(), f3(), currytest(0, 1, 2, 3, 4, 5))
