import os
import sys
import re
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.config import init_or_load_config

class Manager:
    init={
        'Command':{
            'start':['.']
        }
    }
    _dic = init_or_load_config(init)
    start = _dic['Command']['start']
    
    
    _cp = re.compile('([\S]+)([\s\S]*)')
    _cp2 = re.compile('([\S]+)[\s]([\s\S]*)')
    cmds={}

    @staticmethod
    def execute_if(bot, msg):
        '''输入msg, 若是命令则执行并返回True, True将阻止后面继续解析msg, 否则返回False'''
        for start in Manager.start:
            if msg['raw_message'].startswith(start):
                match = Manager._cp.match(msg['raw_message'][len(start):])
                if match:
                    return Manager.execute(bot,match.group(1),match.group(2), msg)
                else:
                    return False
        return False
    
    @staticmethod
    def execute(bot,name,body,msg):
        if name not in Manager.cmds.keys():
            return False
        Manager.cmds[name].run(bot, body, msg)
        return True
    
    @staticmethod
    def register(name,func):
        Manager.cmds[name]=func
    
    @staticmethod
    def cut_s(s:str):
        match = Manager._cp.match(s)
        if match:
            return match.group(1), match.group(2)
        else:
            return False
    
    @staticmethod
    def cut_s2(s:str):
        match = Manager._cp2.match(s)
        if match:
            return match.group(1), match.group(2)
        else:
            return False