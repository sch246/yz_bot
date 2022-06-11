import re

from yz.tool.tool import load_cq

def getdoc(state:str, cls):
    try:
        return cls.docs[state], True
    except:
        return str(cls.__doc__), False
def std_cmd(f):     # 偷懒用的装饰器）
    def cmd(cls, bot, body: str, msg: dict):
        dic = f(cls)
        body = load_cq(body).replace('\r\n','\n')
        Msg=bot.api.Create_Msg(bot,**msg)
        for cp, func in dic.items():
            match = re.match(cp,body)
            if match:
                rtn = func(bot,match)
                if rtn:
                    Msg.send(rtn)
                    return # 这意味着即使匹配成功了，若没有返回值，依旧会继续匹配直到结束
        Msg.send(getdoc(bot.state[0],cls)[0])
    return cmd