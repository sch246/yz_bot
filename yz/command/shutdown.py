import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.api import Create_Msg


class shutdown:
    '''格式: .shutdown
    用途: 关闭bot
    注意: 关闭后得去后台再手动开启才能再次接收消息'''
    docs={
        'run':__doc__
    }
    level=4
    def run(bot, body: str, msg: dict):
        print('关闭中')
        Create_Msg(bot,**msg).send('关闭中')
        def hello_world(bot):
            bot.api.Create_Msg(bot,**msg).send(f'{bot.name}醒啦！')
        bot.storage.add_initfunc('hello_world',hello_world)
        exit()

    def err(bot, body: str, msg: dict):
        return shutdown.run(bot, body, msg)