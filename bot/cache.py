'''用于缓存群和用户的名字啥的，以及群和用户的消息，这应该叫缓存吧）'''

from typing import Callable
from bot.connect import call_api

qq = None
user_names = {}
group_user_infos = {}


def set_self_qq(uid):
    global qq
    qq = uid
def get_self_qq():
    global qq
    return qq

def update(msg):
    user_id = msg['sender']['user_id']
    sender_keys = msg['sender'].keys()
    if 'nickname' in sender_keys:
        nickname = msg['sender']['nickname']
        update_user_name(user_id, nickname)
    if 'group_id' in msg.keys():
        group_id = msg['group_id']
        if 'title' in sender_keys and 'card' in sender_keys:
            title = msg['sender']['title']
            card = msg['sender']['card']
            update_group_user_info(group_id, user_id, title, card)


def update_user_name(user_id: int, name: str):
    user_names[user_id] = name


def update_group_user_info(group_id: int, user_id: int, title: str, card: str):
    if not group_id in group_user_infos.keys():
        group_user_infos[group_id] = {}
    group_user_infos[group_id][user_id] = dict(title=title, card=card)


def get_user_name(user_id: int):
    if user_id in user_names.keys():
        return user_names[user_id]
    else:
        call = call_api('get_stranger_info', user_id=user_id)
        if call['retcode'] == 0:
            name = call['data']['nickname']
            update_user_name(user_id, name)
            return name
        else:
            return '[unknow]'


def get_group_user_info(group_id: int, user_id: int):
    if group_id in group_user_infos.keys() and user_id in group_user_infos[group_id].keys():
        title = group_user_infos[group_id][user_id]['title']
        card = group_user_infos[group_id][user_id]['card']
        return _group_back(user_id, title, card)
    else:
        call = call_api('get_group_member_info', group_id=group_id, user_id=user_id)
        if call['retcode'] == 0:
            data = call['data']
            title = data['title']
            card = data['card']
            update_group_user_info(group_id, user_id, title, card)
            return _group_back(user_id, title, card)
        else:
            return '', '[unknow]'


def _group_back(user_id, title, card):
    if card: # 当card为空时显示昵称
        return title, card
    else:
        return title, get_user_name(user_id)




msgs = {
    'group':{},
    'private':{},
    'bot':[],
    'last':None
}

MAX_LEN = 256

def add_msg(type, uid, msg):
    if not uid in msgs[type].keys():
        msgs[type][uid] = []
    lst = msgs[type][uid]
    lst.insert(0, msg)
    msgs['last'] = msg
    if len(lst)>MAX_LEN:
        lst.pop()

def add_self_msg(msg):
    msgs['bot'].insert(0, msg)
    if len(msgs['bot'])>MAX_LEN:
        msgs['bot'].pop()

def get_last():
    return msgs['last']


'''保存和读取msgs，每个人每个群不会超过256条，所以不用担心无限增长的问题'''
import atexit
from s3.file import write, ensure_file

cache_msgs = 'data/cache_msgs'

def save():
    write(ensure_file(cache_msgs),f'{msgs}')
atexit.register(save)

try:
    msgs = eval(open(cache_msgs, encoding='utf-8').read())
except:
    pass


ops = []
nicknames = []

def get_ops():
    return ops

def _add_op(i):
    if not i in ops:
        ops.append(i)
        return True
    return False
def add_op(uid):
    if isinstance(uid, int):
        return _add_op(uid)
    elif isinstance(uid, str):
        return _add_op(int(uid))
    return False
def del_op(uid):
    if uid in ops:
        ops.remove(uid)
        return True
    return False
def is_op(uid):
    return uid in ops
def set_ops(uids):
    ops.clear()
    ops.extend(uids)


def get_nicknames():
    return nicknames
def get_nickname():
    if len(nicknames)>0:
        return nicknames[0]
def set_nicknames(names):
    nicknames.clear()
    nicknames.extend(names)

