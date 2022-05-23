#!/usr/bin/env python

from email import message
from importlib import reload
import sys
import asyncio
import websockets
import json
from src.tool import *
# from msgexec import *
import src.data as data
# from run import Bot
import pickle
import os


class Storage:
    base_path = './storage/'
    msg_locals = {}
    msg = {}

    @staticmethod
    def save(file_path, obj):
        with open(os.path.join(Storage.base_path, file_path), 'wb') as f:
            pickle.dump(obj, f)

    @staticmethod
    def load(file_path):
        with open(os.path.join(Storage.base_path, file_path), 'wb') as f:
            obj = pickle.load(f)
        return obj

    @staticmethod
    def save_storage():
        Storage.save('Storage.pkl', Storage)

    @staticmethod
    def load_storage():
        Storage = Storage.load('Storage.pkl')


class Msg:
    '''里面是用于在exec_msg的exec运行的函数'''
    @staticmethod
    def send(s):
        s = str(s)
        kargs = {}
        kargs['message'] = s
        if 'group_id' in Storage.msg.keys():
            kargs['group_id'] = Storage.msg['group_id']
            Bot.use_api('send_msg', None, **kargs)
        elif 'user_id' in Storage.msg.keys():
            kargs['user_id'] = Storage.msg['user_id']
            Bot.use_api('send_msg', None, **kargs)

    @staticmethod
    def recv(s, **kargs):
        s = str(s)
        dic = merge_dic(Storage.msg, kargs)
        dic.update({'raw_message': s, 'message': s})
        Bot.recv_event(**dic)


def exec_msg(code: str, msg: dict):
    Storage.msg_locals.update(msg)
    Storage.msg = msg  # 防止有sb命名了msg
    dic = locals()
    dic.update(Storage.msg_locals)
    dic.update({'out':None})
    try:
        exec(code,globals(),dic)
        Msg.send('执行成功，返回'+str(dic['out']))
    except Exception as e:
        Msg.send(str(e))
        print(str(e))
    Storage.msg_locals = dic


def merge_dic(dic0, dic1):
    new_dic = {}
    new_dic.update(dic0)
    new_dic.update(dic1)
    return new_dic

class Bot:
    websocket = None

    @staticmethod
    async def run():
        while True:
            print('尝试连接.. ')
            try:
                async with websockets.connect(data.uri) as websocket:
                    Bot.websocket = websocket
                    try:
                        async for event in websocket:
                            Bot.recv_event(**json.loads(event))
                    except websockets.ConnectionClosed:
                        print('连接关闭', end=' > ')
                        continue
            except ConnectionRefusedError:
                print('连接被拒绝', end=' > ')
                continue

    @staticmethod
    def use_api(action, echo, **kargs):
        try:
            Bot.websocket.send(json.dumps({
                'action': action,
                'echo': echo,
                'params': kargs
            })).send(None)
        except StopIteration:
            pass

    @staticmethod
    def recv_event(**event):
        keys = event.keys()
        if 'post_type' in keys and event['post_type'] == 'message':
            nickname = event['sender']['nickname']
            user_id = event['user_id']
            raw_message = event['raw_message']
            message_id = event['message_id']
            if event['message_type'] == "private":
                print(
                    f"私聊> {nickname}({user_id}): {raw_message} ({message_id})")
                if event['raw_message'] == 'test':
                    print('test')
                    asyncio.to_thread(Bot.websocket.send, data.testmsg)
            elif event['message_type'] == "group":
                group_id = event['group_id']
                print(
                    f"群聊> {group_id} | {nickname}({user_id}): {raw_message} ({message_id})")
            if event['message'].startswith('.py'):
                exec_msg(event['message'][4:], event)
        elif 'meta_event_type' in keys and event['meta_event_type'] == 'heartbeat':
            return
        elif 'status' in keys:
            return
        else:
            print(f"其它> {event}")


if __name__ == "__main__":
    asyncio.run(Bot.run())


