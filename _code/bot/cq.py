if __name__=="__main__":
    # 搞不懂为啥python不能调用上一级的包
    import sys,os
    sys.path.append(os.path.join(os.path.split(__file__)[0], '..'))
from s3.str_tool import replace_dic, replace_dic_reverse

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
    return replace_dic(text, escape_dic)

def unescape(text: str):
    '''将CQ码的一团转义成正常文本'''
    return replace_dic_reverse(text, escape_dic)

def escape2(text: str):
    '''将正常文本转义成CQ码的一团'''
    return replace_dic(text, escape_dic2)

def unescape2(text: str):
    '''将CQ码的一团转义成正常文本'''
    return replace_dic_reverse(text, escape_dic2)

