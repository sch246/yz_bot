'''收藏，并且存储到个人存储中，不支持文件，必须at，不知道能有效多久'''

import re

from main import cache, cq, is_reply, user_storage, connect, is_msg


def run(body:str):
    '''设置名字，回复想要保存的消息，进行标记，不支持文件，必须at，一次仅标记一条消息，不知道能有效多久
格式: [reply:cq_reply].mark set <name:str>
    | .mark
        : get <name:str>
        | list'''
    global msg
    msg = cache.get_last()

    cqs = cq.find_all(body)

    body = cq.unescape(body)
    lines = body.splitlines()
    if not lines:
        lines = ['']
    head = lines[0].strip()
    value = lines[1:]

    m = re.match(r'set ([\S]+)', head)
    if m:
        return _set(m)
    m = re.match(r'get ([\S]+)', head)
    if m:
        return _get(m)

def _set(m):
    storage = user_storage.storage_get(msg['user_id'])
    storage.setdefault('marks',{})
    storage = storage['marks']
    name = m.group(1)
    if is_reply(msg):
        reply_id = cq.load(msg['reply_cq'])['data']['id']
        print(reply_id)
        call2 = connect.call_api('get_msg', message_id=reply_id)
        if not call2['retcode'] == 0:
            return '获取目标消息失败: '+call2['wording']
        reply_msg = call2['data']
        if not is_msg(reply_msg):
            return '不支持mark此类消息'
        storage[name] = reply_msg['message']
        return f'已保存"{name}"'
    else:
        del storage[name]
        return f'已删除"{name}"'

def _get(m):
    storage = user_storage.storage_get(msg['user_id'])
    storage.setdefault('marks',{})
    storage = storage['marks']
    name = m.group(1)
    if name in storage.keys():
        return storage[name]
    else:
        return '对应mark不存在'
