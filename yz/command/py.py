import os
import sys
import traceback
import time as Time
from inspect import getsource as getdef
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__),os.pardir)))
from tool.tool import getlines, to_thread,load_cq,trans_to_cq,add_tab,trans_to_cq,getlines


class py:
    '''格式: .py <code>
    用途: 执行py代码
    给out赋值以决定将返回的结果(默认None)，给back赋值以决定是否返回结果(默认True)
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
            exec(getlines(body,None,-1),globals(),locals())
            out = eval(getlines(body,-1,None),globals(),locals())
            if out:
                Msg.send(str(out))
        except Exception as e:
            Msg.send(getlines(traceback.format_exc(),3,None))
            print(traceback.format_exc())
        bot.storage.msg_locals = locals()
    
    def err(bot, body: str, msg: dict):
        return py.run(bot, body, msg)