import re

from main import str_tool


escape_dic={ # CQ码内的转义
    '&':'&amp;',
    '[':'&#91;',
    ']':'&#93;',
    ',':'&#44;'
}
escape_dic2={ # CQ码外的转义
    '&':'&amp;',
    '[':'&#91;',
    ']':'&#93;'
}


def escape(text: str):
    '''将正常文本转义成CQ码的一团'''
    return str_tool.replace_by_dic(text, escape_dic)

def unescape(text: str):
    '''将CQ码的一团转义成正常文本'''
    return str_tool.replace_by_dic2(text, escape_dic)

def escape2(text: str):
    '''将正常文本转义成CQ码的一团'''
    return str_tool.replace_by_dic(text, escape_dic2)

def unescape2(text: str):
    '''将CQ码的一团转义成正常文本'''
    return str_tool.replace_by_dic2(text, escape_dic2)


re_CQdatas = '(?:,[^,=]+=[^,\]]+)*'

_re_CQ = re.compile(f'\[CQ:[^,\]]+{re_CQdatas}\]')
re_CQ = re.compile(f'\[CQ:(?P<type>[^,\]]+)(?P<data>{re_CQdatas})\]')

def find_all(s:str):
    return _re_CQ.findall(s)

def load(CQ:str):
    '''将字符串形式的单个CQ转化为字典，并且将其中乱七八糟的东东转化为正常'''
    CQ = re.sub('\s','',CQ)  # 去掉空白符
    mt = re_CQ.match(CQ)
    stype=mt.group('type')
    sdata=mt.group('data')
    if sdata:
        # 若CQ有参数,分割并获取参数字符串,再次分割并转化为字典
        str_list = sdata[1:].split(',')
        def f(s:str):
            s = unescape(s)
            i = s.index('=')
            return s[:i], s[i+1:]
        data = dict(map(f ,str_list))
    else:
        data={}
    return {'type':stype,'data':data}

def dump(d:dict):
    '''将字典形式的CQ转化为字符串形式，并且将对应的字符转换为CQ的乱七八糟的东东'''
    type=d['type']
    data = ''.join(map(lambda x:','+escape(f'{x[0]}={x[1]}'), d['data'].items()))
    return f'[CQ:{type}{data}]'