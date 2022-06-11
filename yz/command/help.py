import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.api import Create_Msg
from yz.command.Command import Manager
from tool.cmd import getdoc


class help:
    '''格式: .help [<cmd>]
    用途: 显示全局帮助，或者指定命令的帮助
    使用.cmd list以查看全部命令列表
    使用.help <cmd>以查看对应命令的帮助'''
    docs={
        'run':__doc__
    }
    level=0
    def run(bot, body: str, msg: dict):
        body = body.strip()
        Msg = Create_Msg(bot,**msg)
        if not body:
            body = 'help'
        if body in Manager.cmds.keys():
            s = ''
            if body not in Manager.getcmds(bot).keys():
                s = f'> 该命令不可在当前状态({bot.state[0]})执行\n'
            doc, is_in_state = getdoc(bot.state[0],Manager.cmds[body])
            if is_in_state:
                Msg.send(s+doc)
            else:
                Msg.send(s+'> 正在展示默认doc:\n'+doc)
            return
                
        Msg.send('> 正在展示默认doc:\n'+help.__doc__)
    def err(bot, body: str, msg: dict):
        return help.run(bot, body, msg)

