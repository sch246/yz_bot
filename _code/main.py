'''大概是用来启动bot的主程序，不过直接从这里启动的话，bot将失去重启功能'''
import re
import sys,os
from typing import Generator
import time
from typing import Any


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

import s3.ident as ident
import s3.storage as storage


import bot.connect_with_http as connect
import bot.cmds as cmds
import bot.cq as cq
import bot.data as data

import bot.msgs as msgs
from bot.msgs import *
import bot.cache as cache
import bot.user_storage as user_storage
import bot.chatlog as chatlog



def loc(msg:dict):
    return (msg['group_id'] if 'group_id' in msg.keys() else None, msg['user_id'])


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
    cache.set_ops(config.load_config('ops'))
    cache.set_nicknames(config.load_config('nicknames'))

    login_info = connect.call_api('get_login_info')['data']
    qq, name = login_info['user_id'], login_info['nickname']
    cache.set_self_qq(qq)
    cache.update_user_name(qq, name)
    print(f'{name}({qq})启动了！')

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
    chatlog.write(**self_msg)


# 添加一个对于每个用户和群的待检测列表
catches = {}
# 对命令返回值的处理
def cmd_ret(ret, msg):
    global catches
    msg_loc = loc(msg)
    if isinstance(ret, Generator):
        catches.setdefault(msg_loc, [])
        catches[msg_loc].append(ret)
        try:
            cmd_ret(next(ret), msg)
        except StopIteration as e:
            cmd_ret(e.value, msg)
    elif not ret is None and not ret=='':
        send(ret, **msg)

i = 0
j = 0
k = 0
reply_cq = re.compile(r'^(\[CQ:reply,[^\]]+\])([\S\s]*)')
def recv(msg:dict):
    global i, j, k
    global catches

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

        print(f'[{time.strftime(r"%H:%M:%S")}]【收到消息】',end='')
        chatlog.write(**msg)
        msg_loc = loc(msg)

        if is_msg(msg) and msg['message'].startswith('^'):
            text = msg['message'][1:].rstrip()
            if text in 'Cc':
                del catches[msg_loc]

        if msg_loc in catches.keys() and catches[msg_loc]:
            gen = catches[msg_loc][-1]
            try:
                cmd_ret(gen.send(msg), msg)
            except StopIteration as e:
                cmd_ret(e.value, msg)
                del catches[msg_loc][-1]
            return

        if is_msg(msg):
            text = msg['message']
            # 执行命令
            if text.startswith('.'):
                cmd_ret(cmds.run(text[1:]), msg)
            elif cmd_py.links:
                cmd_py.exec_links()
            # 执行bash
            elif text.startswith('!'):
                os.makedirs('data',exist_ok=True)
                os.system(text[1:]+' > data/tmp.txt')
                with open('data/tmp.txt') as f:
                    s = f.read()
                if not s=='':
                    send(s, **msg)
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


if __name__=="__main__":
    _init_self()
    cmds.load()
    while True:
        recv(connect.recv_msg())