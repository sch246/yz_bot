import sys
import os
import re
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.api import Create_Msg
from yz.command.Command import Manager
from yz.tool.cmd import getdoc


class cmd:
    '''格式: .cmd list
    用途: 编辑和查看命令'''
    docs={
        'run':__doc__
    }
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
        print('CMD')
        s = '[---命令列表---]'
        for k, v in Manager.getcmds(bot).items():
            doc, is_in_state = getdoc(bot.state[0],v)
            if is_in_state:
                sign='●'
            else:
                sign='○'
            s += f'\n{sign} ' + k
            s += '\n' + doc.splitlines()[1]
        return s

    def err(bot, body: str, msg: dict):
        return cmd.run(bot, body, msg)