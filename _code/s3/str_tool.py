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

def stripline(s):
    '''去除空行，兼strip'''
    lst = s.splitlines(True)
    lst = filter(lambda s:s.strip()!='', lst)
    if not lst:
        lst=['']
    return ''.join(lst).strip()

def has_nextline(s:str):
    '''检查字符串中是否有换行'''
    return len((s+'a').splitlines())>1

re_read = re.compile(r'\s+(\S+)([\S\s]*)')
re_read_str = re.compile(r'\s+("[^"]*"|\S+)([\S\s]*)')

def read_params(s:str, count=1, read_str=False):
    '''从字符串中读取空白符后的下n段字符串
    如果要读取引号，则可能抛出异常
    读完后剩余的都是空字符串
    若以非空白符开头，抛出SyntaxError
    返回的字符串数量=count+1，最后一个为剩下的部分'''
    if read_str:
        r = re_read_str.match(s)
    else:
        r = re_read.match(s)
    if not r:
        if not s.strip():    # 全是空白符，或者就是一个空字符串
            return ['']*(count+1)
        raise SyntaxError('需要以空白符开头') # 剩下的唯一可能，引号后接非空白符也会触发
    text, last = r.groups()
    if read_str and len(text)>=2 and text[0] in ['"',"'"] and text[0]==text[1]:
        text = text[1:-1]
    if count==1:
        return text, last
    elif count>1:
        return text, *read_params(last, count-1)
    else:
        raise ValueError('count必须大于0')



LASTLINE = '\33[1A\r\33[K'