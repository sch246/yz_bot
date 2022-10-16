def addtab(s: str, tab='    '):
    '''同样适用于\\r\\n'''
    s = tab + s
    return s.replace('\n', '\n'+tab)


def deltab(s: str, tab='    '):
    '''同样适用于\\r\\n'''
    s = s[len(tab):]
    return s.replace('\n'+tab, '\n')


def replace_dic(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(k, v)
    return s


def replace_dic_reverse(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(v, k)
    return s
