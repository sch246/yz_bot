import os
import sys
import traceback
import time as Time
import math
import random
from inspect import getsource as getdef
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import getlines, to_thread,load_cq,trans_to_cq,add_tab,trans_to_cq,getlines

sqrt = math.sqrt
getran=random.choice

class py:
    '''格式: .py <code>
    用途: 执行py代码
    最后一行将会作为表达式解析，若不是None则会发送消息以返回结果
    让bot发送和接收消息可以用Msg.send(<msg>)和Msg.recv(<msg>)，具体可以使用.py out=Msg.__doc__来查看
    msg是当前被执行消息的msg字典
    可以使用locals()或globals()来查看局部或全局变量'''
    docs={
        'run':__doc__
    }
    level=4
    @to_thread
    def run(bot, body: str, msg: dict):# 想实现，3秒没执行完就timeout异常并退出
        Msg=bot.api.Create_Msg(bot,**msg)
        body = load_cq(body.strip())
        if not body:
            Msg.send(py.__doc__)
            return
        bot.storage.msg = msg  # 防止有sb命名了msg
        bot.storage.msg_locals.update(msg)
        bot.storage.msg_locals['out']=None
        bot.storage.msg_locals['back']=True
        locals().update(bot.storage.msg_locals)
        try:
            exec(getlines(body,None,-1),{**locals(),**globals()},locals())
            out = eval(getlines(body,-1,None),{**locals(),**globals()},locals())
            if out:
                Msg.send(str(out))
        except Exception as e:
            Msg.send(getlines(traceback.format_exc(),3,None))
            print(traceback.format_exc())
        bot.storage.msg_locals = locals()
        # globals().update(locals())
    
    def err(bot, body: str, msg: dict):
        return py.run(bot, body, msg)

class Names:
    """class Names:
    dic={}
    @classmethod
    def set(cls,msg,name):
        cls.dic[msg['user_id']]=name
    @classmethod
    def get(cls,msg):
        names=cls.dic
        if msg['user_id'] in names.keys():
            return names[msg['user_id']]
        if 'group_id' in msg.keys() and 'card' in msg['sender'].keys():
            return msg['sender']['card']
        else:
            return msg['sender']['nickname']"""
    dic={}
    @classmethod
    def set(cls,msg,name):
        cls.dic[msg['user_id']]=name
    @classmethod
    def get(cls,msg):
        names=cls.dic
        if msg['user_id'] in names.keys():
            return names[msg['user_id']]
        if 'group_id' in msg.keys() and 'card' in msg['sender'].keys():
            return msg['sender']['card']
        else:
            return msg['sender']['nickname']

def getname(msg):
    '''def getname(msg):
    return Names.get(msg)'''
    return Names.get(msg)

def isfunc(o):
    return hasattr(o,'__call__')
def notf(f):
    def _f(*args,**kargs):
        return not f(*args,**kargs)
    return _f
def listfunc(o):
    return '\n'.join(filter(isfunc,dir(o)))
def listkey(o):
    return '\n'.join(filter(notf(isfunc),dir(o)))