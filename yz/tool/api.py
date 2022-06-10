import yz.tool.data as data
import yz.tool.tool as tool



class Create_Msg:
    '''里面是用于在exec_msg的exec运行的函数
    
    Msg.send(s:obj)
    用于让bot发送消息
    Msg.recv(s:obj, **kargs)
    用于让bot接收消息，其中**kargs会被作为msg的键解析'''
    def __init__(self,bot,**msg) -> None:
        self.bot=bot
        self.msg=msg
    
    def send(self,s):
        s = str(s)
        kargs = {}
        kargs['message'] = s
        if 'group_id' in self.msg.keys():
            kargs['group_id'] = self.msg['group_id']
            self.bot.use_api('send_msg', **kargs)
        elif 'user_id' in self.msg.keys():
            kargs['user_id'] = self.msg['user_id']
            self.bot.use_api('send_msg', **kargs)
    
    def recv(self,s, **kargs):
        s = str(s)
        dic = tool.merge_dic(self.msg, kargs)
        dic.update({'raw_message': s, 'message': s})
        self.bot.recv_event(**dic)