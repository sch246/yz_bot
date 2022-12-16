'''收藏，并且存储到个人存储中，不支持文件，必须at，不知道能有效多久'''

import re

from main import cache, cq, is_reply, storage, connect, is_msg, chatlog

msg = {}

def run(body:str):
    '''设置名字，回复想要保存的消息，进行标记，不支持文件，必须at，一次仅标记一条消息，不知道能有效多久
格式: [reply:cq_reply].mark set <name:str>
    | .mark
        : get <name:str>
        | list'''
    global msg
    msg = cache.thismsg()

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
    self_storage = storage.get('users',str(msg['user_id']))
    self_storage.setdefault('marks',{})
    self_storage = self_storage['marks']
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
        self_storage[name] = reply_msg
        return f'已保存"{name}"'
    else:
        self_storage.pop(name,None)
        return f'已删除"{name}"'

def _get(m):
    self_storage = storage.get('users',str(msg['user_id']))
    self_storage.setdefault('marks',{})
    self_storage = self_storage['marks']
    name = m.group(1)
    if name in self_storage.keys():
        s = self_storage[name]
        if s.get('sender'):
            s['user_id'] = s['sender']['user_id']
        if s.get('group_id'):
            group_id, user_id = s['group_id'], s['user_id']
            title, name = cache.get_group_user_info(group_id, user_id)
            return chatlog._group_str(title, name, user_id, s['time'], s['message'], s['message_id'])
        else:
            user_id = s['user_id']
            name = cache.get_user_name(user_id)
            return chatlog._private_str(name,s['time'],s['message'],s['message_id'])
    else:
        return '对应mark不存在'
