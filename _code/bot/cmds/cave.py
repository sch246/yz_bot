'''回声洞'''
from random import randint
import re
import time

from main import storage, is_msg, getname, getgroupname, read_params, getran, cache, cq

class Cave:
    def __init__(self) -> None:
        items = storage.get('','cave').items()
        items = sorted(items,key=lambda item:int(item[0]))
        self.msgs = dict(items)
        storage.get_namespace('')['cave'] = self.msgs
        self.pool = storage.get('','cave_pool',list)
    def index(self, i:str=''):
        '''从随机池获取index，抽出后有2/3的概率在随机池消失，随机池抽完后会重置'''
        if i == '':
            if not self.pool:
                self.pool.extend(self.msgs.keys()) #需要确保在原地改动
            idx, i = getran(self.pool, True)
            if randint(0,2):
                del self.pool[idx] # 按照概率删掉
        if i.startswith('-'):
            keys = list(self.msgs.keys())
            i = keys[int(i)+len(keys)] # 如果负数在范围内，则通过
        return i
    def empty(self):
        '''获取空位的index'''
        keys = list(self.msgs.keys())
        keys.sort(key=lambda s:int(s))
        last = int(keys[-1]) if self.msgs else -1
        for i in range(0,last+2):
            if i>=len(keys) or not str(i)==keys[i] and str(i) not in keys:
                break
        return str(i)
    def last(self):
        '''获取最后一个自己设置的cave的index'''
        qq = cache.thismsg()['user_id']
        caves = list(filter(lambda m:qq==m[1].get('qq'),self.msgs.items()))
        if not caves:
            return
        else:
            return caves[-1][0]
    def get(self, i:str):
        '''根据索引返回值'''
        if not self.msgs:
            return '回声洞是空的！'
        if not self.msgs.get(i):
            return '该条消息不存在！'
        s = self.msgs[i]
        if s.get('group'):
            return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['group']}，\n  {s['time']}"
        else:
            return f"{i}:\n{s['text']}\n    ——{s['sender']} 于 {s['time']}"
    def delete(self, i:str):
        if not self.msgs:
            return '回声洞是空的！'
        if not self.msgs.get(i):
            return '该条消息不存在！'
        user_id = cache.thismsg()['user_id']
        if not (user_id in cache.ops or user_id==self.msgs[i].get('qq')):
            return '删除其他人的回声洞需要op'
        del self.msgs[i]
        self.pool.remove(i)
        return f'序号 {i} 删除成功'
    def set(self, i:str, text:str):
        self.msgs[i] = {
            'sender':cq.save_pic(getname()),
            'qq':cache.thismsg()['user_id'],
            'group':getgroupname() if cache.thismsg().get('group_id') else None,
            'time':time.strftime('%Y-%m-%d %H:%M'),
            'text':cq.save_pic(text),
        }
        self.pool.append(i)
        return f'已添加，序号 {i}'

cave  = Cave()
re_int = re.compile(r'(-?\d+)$')

def run(body:str):
    '''回声洞
格式:
.cave [<id:int>]  #获取一条消息
.cave add
 : <msg>    # 放入一条消息
 | || <msg> # 放入一条消息
.cave addn <count:int>
 : || ... # n次
.cave del [<id:int>] # 删除一条消息，默认为上一条消息'''
    s, last = read_params(body)
    if not s or re_int.match(s):
        return cave.get(cave.index(s))
    elif s=='del':
        if not last.strip():
            i = cave.last()
            if i is None:
                return '没有找到你设置的回声洞'
        else:
            s, last = read_params(last)
            if not re_int.match(s):
                return run.__doc__
            i = s
        return cave.delete(cave.index(i))
    elif s=='add':
        text = last.strip()
        if not text:
            reply = yield '发送一条消息，^C以取消'
            if not is_msg(reply):
                return '非消息，执行终止'
            text = reply['message']
        return cave.set(cave.empty(),text)
    elif s=='addn':
        s, last = read_params(last)
        try:
            n = int(s)
        except ValueError:
            return '语法: .cave addn <n:int>'
        if n==0:
            return 'n不能为0'
        elif n<0:
            msgs = cache.get_self_log(cache.thismsg())[1:-n+1]
            text = ''.join(map(lambda m:m['message'], msgs))
            return cave.set(cave.empty(),text)
        elif n>0:
            text = ''
            for i in range(n):
                if i==0:
                    reply = yield f'接下来的{n}条消息将会被合并为1条记录'
                else:
                    reply = yield
                if not is_msg(reply):
                    return '非消息，执行终止'
                text += reply['message']
            if not text:
                return '不知道为啥消息为空'
            return cave.set(cave.empty(),text)
    return run.__doc__
