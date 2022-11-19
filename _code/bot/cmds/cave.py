'''回声洞'''
from random import randint
import re
import time

from main import storage, is_msg, getname, getgroupname, read_params, getran

cave = storage.get('','cave',list)
cave_indexs = [i for i in range(len(cave))]

def ran_index():
    global cave_indexs
    if not cave_indexs:
        cave_indexs = [i for i in range(len(cave))]
    i = getran(cave_indexs)
    if randint(0,1):
        del cave_indexs[i]
    return i

re_int = re.compile(r'(-?\d+)$')

def run(body:str):
    '''回声洞
格式:
.cave [<id:int>]  #获取一条消息
.cave add
 : <msg>    # 放入一条消息
 | || <msg> # 放入一条消息
.cave del [<id:int>] # 删除一条消息，默认为上一条消息'''
    try:
        s, last = read_params(body)
    except SyntaxError as e:
        return e.text
    if not s:
        return _get(ran_index())
    elif re_int.match(s):
        return _get(int(s))
    elif s=='del':
        if not last.strip():
            i = -1
        else:
            s, last = read_params(last)
            if not re_int.match(s):
                return run.__doc__
            i = int(s)
        return _del(i)
    elif s=='add':
        text = last.strip()
        if not text:
            reply = yield '发送一条消息，^C以取消'
            if not is_msg(reply):
                return '非消息，执行终止'
            text = reply['message']
        return _add(text)
    return run.__doc__

def _get(i:int):
    if not cave:
        return '回声洞是空的！'
    if i<0 or i>=len(cave):
        i = i%len(cave)
    s = cave[i]
    if s.get('group'):
        return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['group']}，\n  {s['time']}"
    else:
        return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['time']}"

def _del(i:int):
    if not cave:
        return '回声洞是空的！'
    if i<0 or i>=len(cave):
        i = i%len(cave)
    del cave[i]
    return f'序号 {i} 删除成功，其后的序号会发生变化'

def _add(text:str):
    i = len(cave)
    cave.append({
        'sender':getname(),
        'group':getgroupname(),
        'time':time.strftime('%Y-%m-%d %H:%M'),
        'text':text,
    })
    cave_indexs.append(i)
    return f'已添加，序号 {i}'
