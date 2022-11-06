'''运行python代码的命令，是临时环境，重启后消失,除非最后一行以###开头'''
import traceback
import os, json, time, re

from main import *


msg = {}

def match(s:str):
    msg = cache.get_last()
    return is_msg(msg) and re.match(s, msg['message'])

def getlog():
    return cache.getlog(msg)

def sendmsg(text,_msg=None):
    if _msg is None:
        _msg = cache.get_last()
    send(text,**_msg)

def recvmsg(text, sender_id:int=None, private=None, **kws):
    '''不输入后面的参数时，默认是同一个人同一个位置的recv，否则可以设定对应的sender和group
    当在群内想模拟私聊时，需要设private为True'''
    msg = cache.get_last()
    if sender_id is None:
        sender_id = msg['user_id']
    if private is True:
        msg = msg.copy()
        del msg['group_id']
    recv({**msg, 'user_id':sender_id, 'message':text,'sender':{'user_id': sender_id}, **kws})


def getstorage(_msg=None):
    if _msg is None:
        _msg = msg
    return user_storage.storage_get(_msg['user_id'])


def getname(_msg=None):
    if _msg is None:
        _msg = msg
    name = user_storage.storage_getname(_msg['user_id'])
    if name:
        return name
    if is_group_msg(_msg):
        _, name = cache.get_group_user_info(_msg['group_id'], _msg['user_id'])
    else:
        name = cache.get_user_name(_msg['user_id'])
    return name

def setname(name, _msg=None):
    if _msg is None:
        _msg = msg
    name = user_storage.storage_setname(name, _msg['user_id'])
    return name


try:
    exec(open('data/pyload.py', encoding='utf-8').read())
except:
    pass

loc = {}

@to_thread
def run(body:str):
    '''运行python命令，在.py后空格或换行都行，最后一行的表达式若不是None或注释则会被返回
    默认情况下是临时环境，会在下一次重启后消失
    当最后一行以###开头时，代码将会在每次开启bot时被运行，注意不要写依赖于临时环境的代码
格式: .py <code:pycode>'''
    global msg
    msg = cache.get_last()
    if not msg['user_id'] in cache.get_ops():
        if not cache.any_same(msg, '\.py'):
            send('权限不足(一定消息内将不再提醒)', **msg)
        return
    body = cq.unescape(body.strip())
    if body=='':
        return run.__doc__
    lst = body.splitlines(True)
    try:
        exec(''.join(lst[:-1]), globals(), loc)
        last = lst[-1].strip()
        if last.startswith('###'):
            file.add('data/pyload.py', '\n'+body)
            send('添加成功', **msg)
        elif last.startswith('#'):
            return
        else:
            out = eval(last, globals(), loc)
            if out is not None:
                send(out, **msg)
    except:
        send(''.join(traceback.format_exc().splitlines(True)[3:]), **msg)



# links的部分


# 开头第一个即为link进入的点
root_storage = storage.get_namespace('')
root_storage.setdefault('links',[])
links = root_storage['links']


def lst_remove(lst, value):
    for i in range(len(lst)):
        i = -i-1
        if lst[i]==value:
            del lst[i]


def get_link(linkname:str):
    for link in links:
        if link['name']==linkname:
            return link
def del_link(linkname:str):
    for link in links:
        lst_remove(link['succ'],linkname)
        lst_remove(link['fail'], linkname)
        lst_remove(link['while']['succ'], linkname)
        lst_remove(link['while']['fail'], linkname)
    return _del_link(linkname)
def _del_link(linkname:str):
    '''只删本体不删联系'''
    for i in range(len(links)):
        i = -i-1
        if links[i]['name']==linkname:
            del links[i]
            return True
    return False
def set_link(linkname:str, d:dict):
    for link in links:
        if link['name']==linkname:
            link['cond'] = d['cond']
            link['succ'] = d['succ']
            link['fail'] = d['fail']
            link['action'] = d['action']
            return
    links.append({**d, 'name':linkname})

def connect_link(linkname:dict, tarlinkname:dict, type:str):
    tarlink = get_link(tarlinkname)
    if tarlink and not linkname in tarlink[type]:
        tarlink[type].append(linkname)
def disconnect_link(linkname:dict, tarlinkname:dict, type:str):
    tarlink = get_link(tarlinkname)
    if tarlink and linkname in tarlink[type]:
        lst_remove(tarlink[type], linkname)

# def setroot():
def exec_links():
    global msg
    msg = cache.get_last()
    actions = []
    exec_link(links[0], actions)
    actions_run(actions)


def exec_link(link, actions):
    name = link['name']
    cond = link['cond']
    succ = link['succ']
    fail = link['fail']
    action = link['action']
    code = cq.unescape(cond) # cond仅经过了一次strip
    if code=='':
        return
    lst = code.splitlines(True)
    try:
        exec(''.join(lst[:-1]), globals(), loc)
        last = lst[-1].strip()
        if last.startswith('#'):
            out = None
        else:
            out = eval(last, globals(), loc)
        if out:
            # 如果通过了，运行succ内的link，
            # succ内的link没找到则删掉
            print('触发了link:', name)
            actions.append(action)
            run_or_remove(succ, actions)
        else:
            # 如果没通过，运行fail内的link
            # fail内的link没找到则删掉
            run_or_remove(fail, actions)

    except:
        print(''.join(traceback.format_exc().splitlines(True)[3:]), **msg)

def run_or_remove(linklst:list,actions):
    _del = []
    for linkname in linklst:
        _link = get_link(linkname)
        if not _link:
            _del.append(linkname)
            continue
        exec_link(_link, actions)
    for linkname in _del:
        lst_remove(linklst,linkname)


@to_thread
def actions_run(actions):
    '''actions不需要捕获返回值'''
    for action in actions:
        if action=='':
            return
        action = cq.unescape(action)
        try:
            exec(action, globals(), loc)
        except:
            print(''.join(traceback.format_exc().splitlines(True)[3:]), **msg)