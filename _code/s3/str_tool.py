import re

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

LASTLINE = '\33[1A\r\33[K'