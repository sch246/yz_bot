from random import randint
import re
import time

from main import storage, is_msg, getname, getgroupname

cave = storage.get('','cave',list)


def run(body:str):
    '''回声洞
格式:
.cave [<id:int>]  #获取一条消息
.cave add
 : <msg>    # 放入一条消息
 | || <msg> # 放入一条消息'''
    body = body.strip()
    m = re.match(r'(-?\d+)$', body)
    if m or body=='':
        if not cave:
            return '回声洞是空的！'
        if not m:
            i = randint(0,len(cave)-1)
        else:
            try:
                i = int(m.group(1))
            except:
                return run.__doc__
        if i<0 or i>=len(cave):
            i = i%len(cave)
        s = cave[i]
        if s.get('group'):
            return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['group']}，\n  {s['time']}"
        else:
            return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['time']}"
    elif body.startswith('add'):
        body = body[3:]
        _body = body.lstrip() #add后的玩意
        if _body and body == _body:
            return run.__doc__
        if _body:
            text = _body
        else:
            reply = yield '发送一条消息，^C以取消'
            if not is_msg(reply):
                return '非消息，执行终止'
            text = reply['message']
        cave.append({
            'sender':getname(),
            'group':getgroupname(),
            'time':time.strftime('%Y-%m-%d %H:%M'),
            'text':text,
        })
        return f'已添加，序号 {len(cave)-1}'
    return run.__doc__
