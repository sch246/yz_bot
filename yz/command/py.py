import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import to_thread,merge_dic



@to_thread
def cmd_py(bot, body: str, msg: dict):# 想实现，3秒没执行完就timeout异常并退出
    bot.storage.msg = msg  # 防止有sb命名了msg
    bot.storage.msg_locals.update(msg)
    bot.storage.msg_locals['out']=None
    locals().update(bot.storage.msg_locals)
    Msg=Create_Msg(bot)
    try:
        exec(body)
        Msg.send('执行成功，返回'+str(locals()['out']))
    except Exception as e:
        Msg.send(str(e))
        print(str(e))
    bot.storage.msg_locals = locals()


class Create_Msg:
    '''里面是用于在exec_msg的exec运行的函数'''
    def __init__(self,bot) -> None:
        self.bot=bot
    
    def send(self,s):
        s = str(s)
        kargs = {}
        kargs['message'] = s
        if 'group_id' in self.bot.storage.msg.keys():
            kargs['group_id'] = self.bot.storage.msg['group_id']
            self.bot.use_api('send_msg', None, **kargs)
        elif 'user_id' in self.bot.storage.msg.keys():
            kargs['user_id'] = self.bot.storage.msg['user_id']
            self.bot.use_api('send_msg', None, **kargs)
    
    def recv(self,s, **kargs):
        s = str(s)
        dic = merge_dic(self.bot.storage.msg, kargs)
        dic.update({'raw_message': s, 'message': s})
        self.bot.recv_event(**dic)