import sys
import os
import re
from yz.command.Command import Manager
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.api import Create_Msg


class cmd:
    '''格式: .cmd list
    用途: 编辑和查看命令'''
    level=0
    def run(bot, body: str, msg: dict):
        Msg = Create_Msg(bot,**msg)
        
        link_args={
            ' list':cmd.list_cmd
        }
        
        for k,v in link_args.items():
            cp = re.match(k,body)
            if cp:
                Msg.send(v(bot,cp))
                return
        Msg.send(cmd.__doc__)
        
    def list_cmd(bot,cp):
        s = '\n'.join(['● '+k+'\n'+v.__doc__.splitlines()[0] for k, v in Manager.cmds.items()])
        return '[---命令列表---]\n'+s
