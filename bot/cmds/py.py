'''运行python代码的命令，是临时环境，重启后消失'''
import traceback
from s3.thread import to_thread
from bot.cq import unescape, escape
from bot import send, call_api
from . import msg
from bot.cache import get_ops
import s3.file as file

import os, json

try:
    exec(open('data/pyload.py', encoding='utf-8').read())
except:
    pass

loc = {}

@to_thread
def run(body:str):
    if not msg['user_id'] in get_ops():
        return
    from bot import send
    body = unescape(body.strip())
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
