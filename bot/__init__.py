'''大概是用来启动bot的主程序，不过直接从这里启动的话，bot将失去重启功能'''
import sys,os
from typing import Generator
import time

sys.path.append(os.path.join(os.path.split(__file__)[0], '..'))


from bot.chatlog import write
from bot.connect import *
# import bot.tick
from bot.cache import set_self_qq, update_user_name, set_ops, set_nicknames
from bot.msgs import is_heartbeat, is_msg
import bot.cmds
from s3 import config
from s3.file import ensure_file
from s3.thread import to_thread
from s3 import debug



def I(msg:dict):
    return {'user_id':msg['user_id'], 'group_id': msg['group_id'] if 'group_id' in msg.keys() else None}
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
        msg = recv()
        if is_msg(msg):
            if msg['message'] == f'{check:04d}':
                master = msg['user_id']
                config.save_config([master],'ops')
                send('已成为管理员', user_id=master)
                break
    time.sleep(0.3)
    send('请输入对bot的昵称，不要包含单引号', user_id=master)
    while True:
        msg = recv()
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
    set_ops(config.load_config('ops'))
    set_nicknames(config.load_config('nicknames'))

    login_info = call_api('get_login_info')['data']
    qq, name = login_info['user_id'], login_info['nickname']
    set_self_qq(qq)
    update_user_name(qq, name)
    print(f'{name}({qq})启动了！')

@to_thread
def send(text: Any, user_id: int | str = None, group_id: int | str = None, **params) -> dict:
    '''user_id或者group_id是必须的'''
    debug('【准备发送消息】')
    text = str(text)

    if 'message' in params.keys():
        # 防止message在下面的call_api撞车
        del params['message']
    if user_id == None and group_id == None:
        raise Exception('至少输入一个id!')
    if group_id:
        # 当仅同时传入group和user时保证是群聊
        user_id = None

    call = call_api('send_msg', message=text, user_id=user_id, group_id=group_id, **params)
    if not call['retcode'] == 0:
        print('发送消息失败 '+call['wording'])
        send('发送消息失败\n'+call['wording'], user_id, group_id)
        return

    #------以下是获取自身发送的消息，并且记录下来------#

    call2 = call_api('get_msg', message_id=call['data']['message_id'])
    if not call2['retcode'] == 0:
        print('获取发送的消息失败'+call['wording'])
        return
    self_msg = call2['data']

    if group_id==None:
        self_msg['user_id'] = user_id
    else:
        self_msg['user_id'] = self_msg['sender']['user_id']

    debug(f'【记录消息】', self_msg)
    print('【发送消息】',end='')
    write(**self_msg)


# 添加一个对于每个用户和群的待检测列表
catches = {}
# 对命令返回值的处理
def cmd_ret(ret):
    if isinstance(ret, Generator):
        catches.setdefault(msg_loc, [])
        catches[msg_loc].append(ret)
        try:
            cmd_ret(next(ret))
        except StopIteration as e:
            cmd_ret(e.value)
    elif not ret == None and not ret=='':
        send(ret, **msg)



if __name__=="__main__":
    # loop_thread = start()
    _init_self()
    bot.cmds.load()
    i = 0
    while True:
        msg = recv()
        if msg==None:
            print('连接已断开')
            time.sleep(1)
            continue
            # exit(0)
        # if msg=='exit':
        #     raise '事件循环已断开'
        # if in_debug and (is_heartbeat(msg)):
        #     i += 1
        #     i = i %12
        #     print('.', end='')
        #     if i==0:
        #         print('')
        if not is_heartbeat(msg):
            debug(f'【收到消息】 {msg}')
            # print(write(**msg)[:-1])
            print('【收到消息】',end='')
            write(**msg)
            msg_loc = loc(msg)

            if msg_loc in catches.keys() and catches[msg_loc]:
                gen = catches[msg_loc][-1]
                try:
                    cmd_ret(gen.send(msg))
                except StopIteration as e:
                    cmd_ret(e.value)
                    del catches[msg_loc][-1]
                continue

            if is_msg(msg):
                text = msg['message']
                # 执行命令
                if text.startswith('.'):
                    cmd_ret(bot.cmds.run(text[1:]))
                # 执行bash
                elif text.startswith('!'):
                    ensure_file('data/tmp.txt')
                    os.system(text[1:]+' > data/tmp.txt')
                    with open('data/tmp.txt') as f:
                        s = f.read()
                    if not s=='':
                        send(s, **msg)


# import atexit

# @atexit.register
# def on_exit():
#     # 用于命令重启或关闭时，关闭其它线程
#     from bot.connect import stop
#     stop()
