import re
from typing import Iterable

def addtab(s: str, tab='    '):
    '''适用于\\n，\\r\\n以及\\r'''
    s = tab + s
    return re.sub(r'\r?\n|\r',lambda m:m.group()+tab, s)


def deltab(s: str, tab='    '):
    '''适用于\\n，\\r\\n以及\\r'''
    s = s[len(tab):]
    return re.sub(r'\r?\n|\r'+tab,lambda m:m.group().rstrip(' '), s)


def replace_by_dic(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(k, v)
    return s


def replace_by_dic2(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(v, k)
    return s



re_stc = re.compile(r'{(\D[^:},]*)?:([^}]+)}|{(\D[^:},]*)}')

def _gen_f(loc):
    names = set()
    def f(m:re.Match):
        if m.group(2) is None:
            # 表明没有冒号
            name = m.group(3)
            if name[0].isupper():
                var = r'[\S\s]+'
            else:
                var = r'\S+'
        else:
            name = m.group(1)
            try:
                var = eval(m.group(2), loc)
                if not var:
                    var = ''
                elif isinstance(var, str):
                    pass
                elif isinstance(var,Iterable):
                    var = f'({"|".join(map(str, var))})'
                else:
                    var = str(var)
            except:
                var = m.group(2)
        if name:
            if name not in names:
                names.add(name)
                return f'(?P<{name}>{var})'
            else:
                return f'(?P={name})'
        else:
            return var
    return f

def stc_get(src:str):
    def _(s:str, loc:dict, src=src)->dict|None:
        '''没有match的话返回None，否则返回一个字典(可能为空)'''
        src = re_stc.sub(_gen_f(loc), src)
        m = re.match(src, s)
        if m:
            return m.groupdict()
    return _

def stc_set(tar:str):
    def _(names:dict,tar=tar)->str:
        for k,v in names.items():
            tar = tar.replace(f'{{:{k}}}', v)
        return tar
    return _

def stc(s:str, loc:dict, src:str, tar:str):
    return stc_set(src)(stc_get(tar)(s, loc))


def slice(s:str, start, end):
    lst = s.splitlines(True)
    if not lst:
        lst=['']
    return ''.join(lst[start:end])

def remove_emptyline(s):
    lst = s.splitlines(True)
    lst = filter(lambda s:s.strip()!='', lst)
    if not lst:
        lst=['']
    return ''.join(lst)

LASTLINE = '\33[1A\r\33[K'