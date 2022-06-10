from yz.command.Command import Manager
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import find_all_CQ,load_CQ
from tool.config import save_config

def add_op(bot, user_id):
    bot.op_list.append(user_id)
    save_config(bot.op_list,'Bot','op_list')
def del_op(bot, user_id):
    bot.op_list.remove(user_id)
    save_config(bot.op_list,'Bot','op_list')
def is_op(bot,user_id):
    user_id in bot.op_list

class op:
    '''格式: op QQ号|@某人 [@某人 ...]
    用途: 添加管理员'''
    level=4
    def run(bot, body: str, msg: dict):
        Msg=bot.api.Create_Msg(bot,**msg)
        body = body.strip()
        if body.isdigit():
            if is_op(bot,int(body)):
                Msg.send(f'{int(body)}已是管理员!')
            else:
                add_op(bot, int(body))
                Msg.send(f'已增加管理员: {int(body)}')
        elif find_all_CQ(body):
            lst=[]
            for CQ in find_all_CQ(body):
                CQ = load_CQ(CQ)
                if CQ['type']=='at':
                    if not is_op(bot,int(CQ['data']['qq'])):
                        add_op(bot, int(CQ['data']['qq']))
                        lst.append(int(CQ['data']['qq']))
            if lst:
                Msg.send(f'已增加管理员:{lst}')
            else:
                Msg.send('目标已是管理员，无需增加')
        else:
            Msg.send(op.__doc__)

class deop:
    '''格式: deop QQ号|@某人 [@某人 ...]
    用途: 移除管理员'''
    level=4
    def run(bot, body: str, msg: dict):
        Msg=bot.api.Create_Msg(bot,**msg)
        body = body.strip()
        if body.isdigit():
            if not is_op(bot,int(body)):
                Msg.send(f'{int(body)}不是管理员')
            else:
                del_op(bot, int(body))
                Msg.send(f'已移除管理员: {int(body)}')
        elif find_all_CQ(body):
            lst=[]
            for CQ in find_all_CQ(body):
                CQ = load_CQ(CQ)
                if CQ['type']=='at':
                    if is_op(bot,int(CQ['data']['qq'])):
                        del_op(bot, int(CQ['data']['qq']))
                        lst.append(int(CQ['data']['qq']))
            if lst:
                Msg.send(f'已移除管理员:{lst}')
            else:
                Msg.send('目标中没有管理员')
        else:
            Msg.send(deop.__doc__)


