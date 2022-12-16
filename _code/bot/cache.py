'''用于缓存群和用户的名字啥的，以及群和用户的消息，这应该叫缓存吧）'''

import re
from typing import Callable
from main import connect, file, is_group_msg, is_msg, config



call_api = connect.call_api

qq = None
name = None
names = None  # 只读，包括name和nicknames，对它的修改是非永久的，要改请去改config
user_names = {}
group_user_infos = {}


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


def update_user_name(user_id: int, _name: str):
    user_names[user_id] = _name


def update_group_user_info(group_id: int, user_id: int, title: str, card: str):
    group_user_infos.setdefault(group_id,{})
    group_user_infos[group_id][user_id] = [title, card]


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
    d = group_user_infos.get(group_id,{}).get(user_id)
    if d:
        title, card = d
        return title, card if card else get_user_name(user_id)
    else:
        call = call_api('get_group_member_info', group_id=group_id, user_id=user_id)
        if call['retcode'] == 0:
            data = call['data']
            title, card = data['title'], data['card']
            update_group_user_info(group_id, user_id, title, card)
            return title, card if card else get_user_name(user_id)
        else:
            return '', '[unknow]'

def get_group_name(group_id:int):
    call = call_api('get_group_info', group_id=group_id)
    if call['retcode'] == 0:
        return call['data']['group_name']
    else:
        return '[unknow]'


msgs = {
    'group':{},
    'private':{},
    'bot':[],
    'last':None
}

MAX_LEN = 256

def add_msg(type, uid, msg):
    msgs[type].setdefault(uid,[])
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

msgs_this = {}

def thismsg(msg=None):
    import threading
    ident = threading.get_ident()
    if msg is None:
        return msgs_this[ident]
    else:
        msgs_this[ident] = msg
        return msg

# 保存和读取msgs，每个人每个群不会超过256条，所以不用担心无限增长的问题
import atexit

cache_msgs = file.ensure_file('data/cache_msgs')

def save():
    file.write(cache_msgs,f'{msgs}')
atexit.register(save)

try:
    msgs = eval(open(cache_msgs, encoding='utf-8').read())
except:
    pass


ops = []
nicknames = []

def ops_load():
    global ops
    ops = config.load_config('ops')
def ops_save():
    config.save_config(ops,'ops')

def get_nickname():
    if len(nicknames)>0:
        return nicknames[0]
    return 'bot'
def nicknames_load():
    global nicknames
    nicknames = config.load_config('nicknames')
def nicknames_save():
    config.save_config(nicknames,'nicknames')


def getlog(msg):
    if is_group_msg(msg):
        return msgs['group'][msg['group_id']]
    elif is_msg(msg):
        return msgs['private'][msg['user_id']]
    else:
        return []

def IsSelf(msg):
    def is_self(m):
        return is_msg(m) and m['user_id']==msg['user_id']
    return is_self

def get_self_log(msg):
    return list(filter(IsSelf(msg), getlog(msg)))

def same_times(msg:dict, f:Callable|str, i=None):
    '''判断最近几条消息是否重复'''
    if isinstance(f,str):
        is_self = IsSelf(msg)
        mt = re.compile(f)
        def new_func(_msg):
            return is_self(_msg) and mt.match(_msg['message'])
    else:
        new_func = f
    log_lst = getlog(msg)
    if not (i is None):
        i += 1
        if len(log_lst)<i:
            return False
    return all(new_func(m) for m in log_lst[1:i])

def any_same(msg:dict, f:Callable|str, i=None):
    '''在cache有记录的范围内进行检索'''
    # print('awa')
    if isinstance(f,str):
        is_self = IsSelf(msg)
        mt = re.compile(f)
        def new_func(_msg):
            return is_self(_msg) and mt.match(_msg['message'])
    else:
        new_func = f
    log_lst = getlog(msg)
    if not (i is None):
        i += 1
    return any(new_func(m) for m in log_lst[1:i])

def get_one(msg:dict, f:Callable, i=None):
    if not (i is None):
        i += 1
    for m in getlog(msg)[1:i]:
        if f(m):
            return m


catches = {}


def get(name, type=dict):
    globals().setdefault(name,type())
    return globals()[name]

def set(name,value):
    globals()[name] = value


