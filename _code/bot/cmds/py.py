'''运行python代码的命令，是临时环境，重启后消失,除非最后一行以###开头'''
import traceback
import os, json, time, re, random

from main import *


msg = {}

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
        send(''.join(traceback.format_exc().splitlines(True)[3:]).strip(), **msg)


def match(s:str):
    if is_msg(msg):
        return re.match(s, msg['message'])

def getlog(i=None):
    if i is None:
        return cache.getlog(msg)
    else:
        return cache.getlog(msg)[i]

def sendmsg(text,**_msg):
    if not _msg:
        _msg = msg
    send(text,**_msg)

def recvmsg(text, sender_id:int=None, private=None, **kws):
    '''不输入后面的参数时，默认是同一个人同一个位置的recv，否则可以设定对应的sender和group
    私聊想模拟群内，只需要加group_id=xx
    当在群内想模拟私聊时，需要设private为True'''
    if sender_id is None:
        sender_id = msg['user_id']
    if private is True:
        msg = msg.copy()
        del msg['group_id']
    recv({**msg, 'user_id':sender_id, 'message':text,'sender':{'user_id': sender_id}, **kws})


def getstorage(user_id=None):
    if user_id is None:
        user_id = msg['user_id']
    return user_storage.storage_get(user_id)


def getname(user_id=None, group_id=None):
    if user_id is None:
        user_id = msg['user_id']
    if group_id is None and is_group_msg(msg):
        group_id = msg['group_id']
    name = user_storage.storage_getname(user_id)
    if name:
        return name
    if is_group_msg(msg):
        _, name = cache.get_group_user_info(group_id, user_id)
    else:
        name = cache.get_user_name(user_id)
    return name

def setname(name, user_id=None):
    if user_id is None:
        user_id = msg['user_id']
    name = user_storage.storage_setname(name, user_id)
    return name


def msglog(i=0):
    msg = getlog()[i]
    if is_msg(msg):
        return msg['message']

def getran(lst:list):
    if lst:
        return lst[random.randint(0, len(lst)-1)]

# 接收文件！
recv_file = cmds.modules['file']._recv_file
send_file = cmds.modules['file']._send_file

def same_times(f:Callable|str, i=None):
    return cache.same_times(cache.get_last(), f, i)
def any_same(f:Callable|str, i=None):
    return cache.any_same(cache.get_last(), f, i)
def get_one(f:Callable, i=None):
    return cache.get_one(cache.get_last(), f, i)









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
    link = get_link(linkname)
    if link and not tarlinkname in link['while'][type]:
        link['while'][type].append(tarlinkname)
def disconnect_link(linkname:dict, tarlinkname:dict, type:str):
    tarlink = get_link(tarlinkname)
    if tarlink and linkname in tarlink[type]:
        lst_remove(tarlink[type], linkname)
    link = get_link(linkname)
    if link and tarlinkname in link['while'][type]:
        lst_remove(link['while'][type], tarlinkname)

# def setroot():
@to_thread
def exec_links():
    global msg
    msg = cache.get_last()
    exec_link(links[0])


def exec_link(link):
    name = link['name']
    cond = link['cond']
    succ = link['succ']
    fail = link['fail']
    action = link['action']
    code = cq.unescape(cond) # cond仅经过了一次strip
    if code=='':
        return
    lst = code.splitlines(True)
    out = None
    try:
        exec(''.join(lst[:-1]), globals(), loc)
        last = lst[-1].strip()
        if last.startswith('#'):
            out = None
        else:
            out = eval(last, globals(), loc)

    except:
        print(''.join(traceback.format_exc().splitlines(True)[3:]), **msg)
    if out:
        # 如果通过了，运行succ内的link，
        # succ内的link没找到则删掉
        print('触发了link:', name)
        _run_action(action)
        run_or_remove(succ)
    else:
        # 如果没通过，运行fail内的link
        # fail内的link没找到则删掉
        run_or_remove(fail)

def run_or_remove(linklst:list):
    _del = []
    for linkname in linklst:
        _link = get_link(linkname)
        if not _link:
            _del.append(linkname)
            continue
        exec_link(_link)
    for linkname in _del:
        lst_remove(linklst,linkname)



def _run_action(action):
    '''actions不需要捕获返回值'''
    if action=='':
        return
    action = cq.unescape(action)
    try:
        exec(action, globals(), loc)
    except:
        print(''.join(traceback.format_exc().splitlines(True)[3:]), **msg)

def run_action(linkname):
    _run_action(get_link(linkname)['action'])
do_action = run_action

def formats_link(link:dict, mode=0):
    '''输出link的显示用字符串形式'''
    lst = [link['name']]
    if mode==0:
        if link['succ']:
            lst.append('    succ')
            lst.extend(map(lambda s:'        '+s, link['succ']))
        if link['fail']:
            lst.append('    fail')
            lst.extend(map(lambda s:'        '+s, link['fail']))
    elif mode==1:
        lst.append('    cond')
        lst.append(str_tool.addtab(link['cond'],tab='        '))
        lst.append('    action')
        lst.append(str_tool.addtab(link['action'],tab='        '))
    return '\n'.join(lst)
