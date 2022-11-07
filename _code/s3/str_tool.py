import re

def addtab(s: str, tab='    '):
    '''适用于\\n，\\r\\n以及\\r'''
    s = tab + s
    return re.sub(r'\r?\n|\r',lambda m:tab+m.group(), s)


def deltab(s: str, tab='    '):
    '''适用于\\n，\\r\\n以及\\r'''
    s = s[len(tab):]
    return re.sub(tab+r'\r?\n|\r',lambda m:m.group()[len(tab):], s)


def replace_by_dic(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(k, v)
    return s


def replace_by_dic2(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(v, k)
    return s
