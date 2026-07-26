'''并没有啥用，备用'''

re_need_trans=['\\', '*', '.', '?', '+', '^', '$', '|',  '/', '[', ']', '(', ')', '{', '}', ]

def strip_re(text:str):
    print(text)
    for c in re_need_trans:
        text = text.replace(c, '\\'+c)
    return text