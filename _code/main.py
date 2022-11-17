'''大概是用来启动bot的主程序，不过直接从这里启动的话，bot将失去重启功能'''
import re
import sys,os
from typing import Generator
import time,random
from typing import Any
from queue import Queue


from s3 import *
import s3.config as config
import s3.counter as counter
import s3.file as file
# import s3.log as log
import s3.params as params
import s3.schedule as schedule
import s3.str_tool as str_tool
import s3.thread as thread
from s3.thread import to_thread
import s3.mcrcon as mcrcon

import s3.linux_screen as screen
import s3.mc as mc
import s3.ident as ident
import s3.storage as storage


import bot.connect_with_http as connect
import bot.cmds as cmds
import bot.cq as cq
import bot.data as data

import bot.msgs as msgs
from bot.msgs import *
import bot.cache as cache
import bot.chatlog as chatlog


def msg_id(msg:dict):
    return (msg.get('group_id'), msg['user_id'])


def first_start():
    '''第一次加载'''
    config.init_config()
    print('未检测到config，第一次加载中')
    from random import randint
    check = randint(0, 9999)
    print(f'私聊bot验证码以确定master: {check:04d}')
    while True:
        msg = connect.recv_msg()
        if is_msg(msg):
            if msg['message'] == f'{check:04d}':
                master = msg['user_id']
                config.save_config([master],'ops')
                send('已成为管理员', user_id=master)
                break
    time.sleep(0.3)
    send('请输入对bot的昵称，不要包含单引号', user_id=master)
    while True:
        msg = connect.recv_msg()
        if is_msg(msg) and msg['user_id']==master:
            name = msg['message']
            if "'" not in name:
                config.save_config([name],'nicknames')
                send(f'昵称已设置为【{name}】', user_id=master)
                break
            send('请输入对bot的昵称，不要包含单引号', user_id=master)
    time.sleep(0.3)
    send('设置完毕！', user_id=master)

def _init_self():
    # 加载设置
    if not os.path.isfile('config.json'):
        first_start()
    cache.ops_load()
    cache.nicknames_load()

    login_info = connect.call_api('get_login_info')['data']
    qq, name = login_info['user_id'], login_info['nickname']
    cache.set('qq',qq)
    cache.set('name',name)
    cache.set('names',set([*cache.nicknames,name]))
    cache.update_user_name(qq, name)
    print(f'{name}({qq})启动了！')
    if 'auto_reboot' in sys.argv[1:]:
        print('自动重启已开启')
    if 'debug' in sys.argv[1:]:
        print('debug模式')

@to_thread
def send(text: Any, user_id: int | str = None, group_id: int | str = None, **params) -> dict:
    '''user_id或者group_id是必须的'''
    debug('【准备发送消息】')
    text = str(text)

    if 'message' in params.keys():
        # 防止message在下面的call_api撞车
        del params['message']
    if user_id is None and group_id is None:
        raise Exception('至少输入一个id!')
    if group_id:
        # 当仅同时传入group和user时保证是群聊
        user_id = None

    call = connect.call_api('send_msg', message=text, user_id=user_id, group_id=group_id, **params)
    if not call['retcode'] == 0:
        print('发送消息失败 '+call['wording'])
        send('发送消息失败\n'+call['wording'], user_id, group_id)
        return

    #------以下是获取自身发送的消息，并且记录下来------#

    call2 = connect.call_api('get_msg', message_id=call['data']['message_id'])
    if not call2['retcode'] == 0:
        print('获取发送的消息失败'+call2['wording'])
        return
    self_msg = call2['data']

    if group_id is None:
        self_msg['user_id'] = user_id
    else:
        self_msg['user_id'] = self_msg['sender']['user_id']

    print(f'[{time.strftime(r"%H:%M:%S")}]【发送消息】',end='')
    chatlog.write(self_msg)



# 对命令返回值的处理
def cmd_ret(ret, msg):
    if isinstance(ret, Generator):
        catches = cache.get('catches') # 对每个输入区域的检测
        msg_loc = msg_id(msg)
        try:
            if msg_loc not in catches.keys():
                cmd_ret(next(ret), msg)
                catches[msg_loc] = ret
            else:
                cmd_ret(ret.send(msg), msg)
        except StopIteration as e:
            catches.pop(msg_loc,None)
            cmd_ret(e.value, msg)
    elif not ret is None and not ret=='':
        send(ret, **msg)

i = 0
j = 0
k = 0
reply_cq = re.compile(r'^(\[CQ:reply,[^\]]+\])([\S\s]*)')
at_cq = re.compile(r'^(\[CQ:at,[^\]]+\])([\S\s]*)')
def recv(msg:dict):
    global i, j, k

    cmd_py = cmds.modules['py']

    if msg is None:
        print('连接已断开')
        time.sleep(1)
        return
    if not is_heartbeat(msg):
        i, j, k = 0, 0, 0

        if is_msg(msg):
            m = reply_cq.match(msg['message'])
            if m:
                msg['reply_cq'] = m.group(1)
                msg['message'] = m.group(2).lstrip()
                m = at_cq.match(msg['message'])
                if m:
                    msg['at_cq'] = [m.group(1)]
                    msg['message'] = m.group(2).lstrip()
            m = at_cq.match(msg['message'])
            if m:
                msg.setdefault('at_cq',[])
                msg['at_cq'].append(m.group(1))
                msg['message'] = m.group(2).lstrip()

        print(f'[{time.strftime(r"%H:%M:%S")}]【收到消息】',end='')
        chatlog.write(msg)
        msg_loc = msg_id(msg)


        if is_msg(msg) and msg['message'].startswith('^'):
            text = msg['message'][1:].rstrip()
            if text in 'Cc':
                del catches[msg_loc]

        catches = cache.get('catches')
        if catches.get(msg_loc):
            c = catches[msg_loc]
            if isinstance(c,Queue):
                c.put(msg)
                del catches[msg_loc]
            else:
                cmd_ret(c, msg)
            return

        if is_msg(msg):
            text = msg['message']
            # 执行命令
            if text.startswith('.') and cmds.is_cmd(text[1:]):
                cmd_ret(cmds.run(*cmds.is_cmd(text[1:])), msg)
            # 执行bash
            elif text.startswith('!'):
                s = os.popen(text[1:]).read()
                if not s=='':
                    send(s, **msg)
            elif cmd_py.links:
                print('进入links')
                cmd_py.exec_links()
    else:
        # 非常傻逼地用ijk作计时器，需要将心跳间隔设置为5s)
        # 虽然这玩意不准，不过用来大致度量时间还是可以的
        if i+j+k>0:
            print(str_tool.LASTLINE,end='')
        i += 1
        if i==12:
            j += 1
        if j==60:
            k += 1
        i %= 12
        j %= 60
        if j==0 and k==0:
            print(f'.{i}')
        elif j!=0 and k==0:
            print(f'|{j}.{i}')
        elif k!=0:
            print(f'█{k}|{j}.{i}')




def match(s:str):
    '''判断当前的消息是否通过某正则表达式，当前消息必须为文本消息'''
    msg = cache.get_last()
    if is_msg(msg):
        return re.match(s, msg['message'])

def getlog(i=None):
    '''获取这个聊天区域的消息列表，由于是cache存的，默认只会保存最多256条'''
    msg = cache.get_last()
    if i is None:
        return cache.getlog(msg)
    else:
        return cache.getlog(msg)[i]

def sendmsg(text,**_msg):
    '''发送消息，就是可以省略后续参数而已'''
    msg = cache.get_last()
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
    '''获取个人的存储字典'''
    msg = cache.get_last()
    if user_id is None:
        user_id = msg['user_id']
    return storage.get('users',str(user_id))


def getname(user_id=None, group_id=None):
    '''获取名字，如果有设置名字就返回设置的名字，反正无论如何都会获得一个'''
    msg = cache.get_last()
    if user_id is None:
        user_id = msg['user_id']
    if group_id is None and is_group_msg(msg):
        group_id = msg['group_id']
    name = storage.get('users',str(user_id)).get('name')
    if name:
        return name
    if is_group_msg(msg):
        _, name = cache.get_group_user_info(group_id, user_id)
    else:
        name = cache.get_user_name(user_id)
    return name

def setname(name, user_id=None):
    '''设置名字，将会把名字存进个人存储字典中，可以被获取名字的函数获取'''
    msg = cache.get_last()
    if user_id is None:
        user_id = msg['user_id']
    name = storage.get('users',str(user_id))['name'] = name
    return name

def getgroupname(group_id=None):
    '''获取名字，如果有设置名字就返回设置的名字，反正无论如何都会获得一个'''
    msg = cache.get_last()
    if group_id is None and is_group_msg(msg):
        group_id = msg['group_id']
    if not group_id:
        return
    name = storage.get('groups',str(group_id)).get('name')
    if name:
        return name
    else:
        return cache.get_group_name(group_id)

def setgroupname(name, group_id=None):
    '''设置名字，将会把名字存进群存储字典中，可以被获取名字的函数获取'''
    msg = cache.get_last()
    if group_id is None:
        group_id = msg['group_id']
    if not group_id:
        return
    storage.get('groups',str(group_id))['name'] = name
    return name


def msglog(i=0):
    '''按索引获取文本消息，不会获取到其它类型的信息，若索引超出范围则返回None
    通常来讲默认会返回本条消息(本条消息肯定是文本啦)'''
    _i = 0
    for msg in getlog():
        if is_msg(msg):
            if _i == i:
                return msg['message']
            _i += 1

def getran(lst:list):
    '''随机取出列表中的元素'''
    if lst:
        return lst[random.randint(0, len(lst)-1)]



if __name__=="__main__":
    _init_self()
    cmds.load()
    while True:
        recv(connect.recv_msg())