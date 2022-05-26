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
    level=9
    def run(bot, body: str, msg: dict):
        Msg=bot.api.Create_Msg(bot,**msg)
        body = body.strip()
        if body.isdigit():
            add_op(bot, int(body))
        elif find_all_CQ(body):
            for CQ in find_all_CQ(body):
                CQ = load_CQ(CQ)
                if CQ['type']=='at':
                    add_op(bot, CQ['data']['qq'])
        else:
            Msg.send('格式: op QQ号|@某人 [@某人 ...]')

class deop:
    level=9
    def run(bot, body: str, msg: dict):
        Msg=bot.api.Create_Msg(bot,**msg)
        body = body.strip()
        if body.isdigit():
            del_op(bot, int(body))
        elif find_all_CQ(body):
            for CQ in find_all_CQ(body):
                CQ = load_CQ(CQ)
                if CQ['type']=='at':
                    del_op(bot, CQ['data']['qq'])
        else:
            Msg.send('格式: deop QQ号|@某人 [@某人 ...]')


