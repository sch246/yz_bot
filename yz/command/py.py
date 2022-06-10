import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import to_thread,load_cq



class py:
    level=4
    @to_thread
    def run(bot, body: str, msg: dict):# 想实现，3秒没执行完就timeout异常并退出
        bot.storage.msg = msg  # 防止有sb命名了msg
        bot.storage.msg_locals.update(msg)
        bot.storage.msg_locals['out']=None
        bot.storage.msg_locals['back']=True
        locals().update(bot.storage.msg_locals)
        Msg=bot.api.Create_Msg(bot,**msg)
        try:
            exec(load_cq(body.strip()),globals(),locals())
            if locals()['back']:
                Msg.send('执行成功，返回'+str(locals()['out']))
        except Exception as e:
            Msg.send(str(e))
            print(e)
        bot.storage.msg_locals = locals()
