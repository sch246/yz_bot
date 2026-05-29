'''回声洞'''
from random import randint
import re
import json
import os
import time

from main import storage, is_msg, getname, getgroupname, read_params, getran, cache, cq, str_tool, sendmsg, pages

# 启动时的 cave 快照
_cave_startup_snapshot = json.dumps({
    'msgs': storage.get('', 'cave'),
    'pool': storage.get('', 'cave_pool', list),
}, ensure_ascii=False, sort_keys=True)

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

    def search(self, keyword):
        '''根据关键词搜索cave内容'''
        if not self.msgs:
            return '回声洞是空的！'

        results = []
        for idx, msg in self.msgs.items():
            if keyword.lower() in msg['text'].lower():
                # 添加简短预览，最多显示20个字符
                preview = msg['text']
                t = msg['time']
                if len(preview) > 20:
                    preview = preview[:20] + "..."
                preview = '    '+str_tool.addtab(preview)
                results.append(f"{idx} | {t}:\n{preview}")

        if not results:
            return f'未找到包含关键词 "{keyword}" 的消息'

        sendmsg(f'找到 {len(results)} 条包含 "{keyword}" 的消息:')
        return pages.display(results, 10)

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
.cave del [<id:int>] # 删除一条消息，默认为上一条消息
.cave save [<path>]   # 导出到文件
.cave load [<path>]   # 从文件导入'''
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
            text = ''.join(map(lambda m:m['message'], reversed(msgs)))
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
    elif s=='search':
        keyword = last.strip()
        if not keyword:
            return '请输入要搜索的关键词'
        return cave.search(keyword)
    elif s=='save':
        path, _ = read_params(last)
        return _save_cave(path.strip() or 'data/cave_save.json')
    elif s=='load':
        path, _ = read_params(last)
        return _load_cave(path.strip() or 'data/cave_save.json')
    return run.__doc__


# ---- cave 配置的导入/导出 ----

CAVE_MSG_REQUIRED_KEYS = ('sender', 'qq', 'time', 'text')

def _save_cave(path: str):
    '''保存当前 cave 到文件'''
    data = {
        'msgs': cave.msgs,
        'pool': cave.pool,
    }
    try:
        current = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return f'序列化失败: {e}'

    if _cave_startup_snapshot != current:
        reply = yield f'当前 cave 与启动时不同（{len(cave.msgs)} 条），确认覆盖 {path}？(y/n)'
        if not (is_msg(reply) and reply['message'].strip().lower() == 'y'):
            return '操作取消'

    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    return f'已保存 {len(cave.msgs)} 条回声洞到 {path}'


def _load_cave(path: str):
    '''从文件加载 cave，验证后替换。失败则保留原数据'''
    if not os.path.exists(path):
        return f'文件不存在: {path}'

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return f'读取失败: {e}'

    if not isinstance(data, dict):
        return f'格式错误: 期望 JSON 对象，得到 {type(data).__name__}'
    if 'msgs' not in data or not isinstance(data['msgs'], dict):
        return '格式错误: 缺少 msgs 字段或不是对象'
    if 'pool' not in data or not isinstance(data['pool'], list):
        return '格式错误: 缺少 pool 字段或不是列表'

    # 逐条验证 msgs
    errors = []
    for key, msg in data['msgs'].items():
        if not isinstance(msg, dict):
            errors.append(f'msgs[{key}]: 不是对象')
            continue
        for k in CAVE_MSG_REQUIRED_KEYS:
            if k not in msg:
                errors.append(f'msgs[{key}]: 缺少字段 {k!r}')

    if errors:
        return f'验证失败，保留当前数据 ({len(cave.msgs)} 条):\n' + '\n'.join(errors[:10]) + (
            f'\n... 共 {len(errors)} 处错误' if len(errors) > 10 else ''
        )

    # 确认替换
    reply = yield f'将用 {len(data["msgs"])} 条替换当前 {len(cave.msgs)} 条，确认？(y/n)'
    if not (is_msg(reply) and reply['message'].strip().lower() == 'y'):
        return '操作取消'

    cave.msgs.clear()
    cave.msgs.update(data['msgs'])
    cave.pool[:] = data['pool']
    storage.save()
    global _cave_startup_snapshot
    _cave_startup_snapshot = json.dumps({
        'msgs': cave.msgs,
        'pool': cave.pool,
    }, ensure_ascii=False, sort_keys=True)
    return f'已加载 {len(cave.msgs)} 条回声洞，数据已保存到磁盘'
