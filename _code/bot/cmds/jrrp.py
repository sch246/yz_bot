'''今日人品'''
import time
from main import getstorage, rd, getran, sendmsg, cache, is_msg

大成功 = ['(๑´∀`๑)','Ｏ(≧▽≦)Ｏ','＼（￣︶￣）／']
大失败 = ['=͟͟͞͞(꒪ᗜ꒪ ‧̣̥̇)','( ๑ŏ ﹏ ŏ๑ )','(ó﹏ò｡) ']

TIP = '''在查看结果前，请先同意以下附加使用条款：
1. 我知晓并了解柚子的今日人品功能完全没有出Bug。
2. 柚子及它的主人不对使用本功能所间接造成的一切财产损失(如砸电脑等)等负责。'''

def run(body:str):
    '''今日人品
格式: .jrrp'''
    if_setzero = False
    if body.strip():
        if body.strip()=='zero':
            if_setzero = True
        else:
            return run.__doc__
    date = time.strftime('%y-%m-%d')
    data = getstorage()
    if_show = True
    if not data.get('jrrp_date')==date:
        # 如果今天第一次运行
        data['jrrp_'] = rd(1,101) -1 if not if_setzero else 0
        r = data['jrrp_']

        if r>95:data['jrrp']=f'{r}\n{getran(大成功)}'
        elif r<5:
            data['jrrp'] = f'{r}\n{getran(大失败)}'
            reply = yield TIP
            if not (is_msg(reply) and reply['message'].strip()=='.jrrp'):
                if_show = False
        else:
            data['jrrp'] = str(r)

        if time.strftime('%m-%d')!='04-01':
            # 4月1号人品不固定
            data['jrrp_date'] = date
    if if_show:
        return data['jrrp']
