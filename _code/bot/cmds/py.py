'''运行python代码的命令，是临时环境，重启后消失'''
import traceback
import os, json, time

from main import *


msg = {}

def sendmsg(text,_msg=None):
    if _msg==None:
        _msg = msg
    send(text,**_msg)


def getstorage(_msg=None):
    if _msg==None:
        _msg = msg
    return user_storage.storage_get(_msg['user_id'])


def getname(_msg=None):
    if _msg==None:
        _msg = msg
    name = user_storage.storage_getname(_msg['user_id'])
    if name:
        return name
    if is_group_msg(_msg):
        _, name = cache.get_group_user_info(_msg['group_id'], _msg['user_id'])
    else:
        name = cache.get_user_name(_msg['user_id'])
    return name

def setname(name, _msg=None):
    if _msg==None:
        _msg = msg
    name = user_storage.storage_setname(name, _msg['user_id'])
    return name

def getlog():
    if is_group_msg(msg):
        return cache.msgs['group'][msg['group_id']]
    elif is_msg(msg):
        return cache.msgs['private'][msg['user_id']]
    else:
        return []

try:
    exec(open('data/pyload.py', encoding='utf-8').read())
except:
    pass

loc = {}

@to_thread
def run(body:str):
    global msg
    msg = cache.get_last()
    if not msg['user_id'] in cache.get_ops():
        return
    body = cq.unescape(body.strip())
    if body=='':
        return
    lst = body.splitlines(True)
    try:
        exec(''.join(lst[:-1]), globals(), loc)
        last = lst[-1].strip()
        if last.startswith('###'):
            file.add('data/pyload.py', '\n'+body)
            send('添加成功', **msg)
        else:
            out = eval(last, globals(), loc)
            if out:
                send(out, **msg)
    except:
        send(''.join(traceback.format_exc().splitlines(True)[3:]), **msg)
