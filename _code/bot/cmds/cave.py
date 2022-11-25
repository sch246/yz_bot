'''回声洞'''
from random import randint
import re
import time

from main import storage, is_msg, getname, getgroupname, read_params, getran, cache

cave = storage.get('','cave')
cave_pool = storage.get('','cave_pool',list)
last = int(list(cave.keys())[-1]) if cave else -1

def ran_index() -> str:
    global cave_pool
    if not cave_pool:
        cave_pool.extend(cave.keys())
    idx, i = getran(cave_pool, True)
    if randint(0,2):
        del cave_pool[idx]
    return i

def get_ne(i):
    if i == '-1':
        i = str(last)
    else:
        keys = list(cave.keys())
        if int(i) >= -len(keys):
            i = keys[int(i)]
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
        return _get(s)
    elif s=='del':
        if not last.strip():
            i = '-1'
        else:
            s, last = read_params(last)
            if not re_int.match(s):
                return run.__doc__
            i = s
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

def _get(i:str):
    if not cave:
        return '回声洞是空的！'
    if i.startswith('-'):
        i = get_ne(i)
    if not cave.get(i):
        return '该条消息不存在！'
    s = cave[i]
    if s.get('group'):
        return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['group']}，\n  {s['time']}"
    else:
        return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['time']}"

def _del(i:str):
    if not cave:
        return '回声洞是空的！'
    if i.startswith('-'):
        i = get_ne(i)
    if not cave.get(i):
        return '该条消息不存在！'
    user_id = cache.get_last()['user_id']
    if not (user_id in cache.ops or user_id==cave[i].get('qq')):
        return '删除其他人的回声洞需要op'
    global last
    if i == str(last):
        last -= 1
    del cave[i]
    return f'序号 {i} 删除成功'

def _add(text:str):
    global last
    last += 1
    i = str(last)
    cave[i] = {
        'sender':getname(),
        'qq':cache.get_last()['user_id'],
        'group':getgroupname(),
        'time':time.strftime('%Y-%m-%d %H:%M'),
        'text':text,
    }
    cave_pool.append(i)
    return f'已添加，序号 {i}'
