import sys
import os
from yz.command.Command import Manager
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.api import Create_Msg


class help:
    '''格式: .help [<cmd>]
    用途: 显示全局帮助，或者指定命令的帮助
    使用.cmd list以查看全部命令列表
    使用.help <cmd>以查看对应命令的帮助'''
    level=0
    def run(bot, body: str, msg: dict):
        body = body.strip()
        Msg = Create_Msg(bot,**msg)
        if not body:
            Msg.send(help.__doc__)
            return
        if body in Manager.cmds.keys():
            Msg.send(Manager.cmds[body].__doc__)
        
