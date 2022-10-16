'''用于连接bot'''

import requests
import socket
import json
from typing import Any

# https://blog.csdn.net/qq_27694835/article/details/108613607
# Requests 模块 https://www.cnblogs.com/saneri/p/9870901.html

url = 'http://127.0.0.1:5700'


def call_api(action: str, **params) -> dict:
    headers = {
        'Content-Type': 'application/json'
    }
    re = requests.post(
        url+f'/{action}', headers=headers, json=params, verify=False)
    return json.loads(re.text)


def send_msg(msg: str, user_id: int | str = None, group_id: int | str = None, **params) -> dict:
    '''user_id或者group_id是必须的'''
    if user_id == None and group_id == None:
        raise Exception('至少输入一个id!')
    return call_api('send_msg', message=msg, user_id=user_id, group_id=group_id, **params)


# 以下copy自https://zhuanlan.zhihu.com/p/404342876

ListenSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ListenSocket.bind(('127.0.0.1', 5701))
ListenSocket.listen(100)  # 传入的参数指定等待连接的最大数量

HttpResponseHeader = '''HTTP/1.1 200 OK\r\n
Content-Type: text/html\r\n\r\n
'''


def request_to_json(msg: str) -> dict | None:
    '''遍历request字符串, 直到后面是json格式, 读取对应json文本并返回'''
    for i in range(len(msg)):
        if msg[i] == "{" and msg[-1] == "\n":
            return json.loads(msg[i:])
    return None

# 需要循环执行，返回值为json格式


def rev_msg() -> dict | None:
    Client, Address = ListenSocket.accept()
    Request = Client.recv(1024).decode(encoding='utf-8')
    rev_json = request_to_json(Request)
    # 发送信号表示我收到了
    Client.sendall(HttpResponseHeader.encode(encoding='utf-8'))
    Client.close()
    return rev_json


# while True:
#     rev = rev_msg()
#     if rev["post_type"] == "message":
#         # print(rev) #需要功能自己DIY
#         if rev["message_type"] == "private":  # 私聊
#             if rev['raw_message'] == '在吗':
#                 qq = rev['sender']['user_id']
#                 print(send_msg('我在', user_id=qq))
#         elif rev["message_type"] == "group":  # 群聊
#             group = rev['group_id']
#             if "[CQ:at,qq=机器人的QQ号]" in rev["raw_message"]:
#                 if rev['raw_message'].split(' ')[1] == '在吗':
#                     qq = rev['sender']['user_id']
#                     send_msg({'msg_type': 'group', 'number': group,
#                              'msg': '[CQ:poke,qq={}]'.format(qq)})
#         else:
#             continue
#     else:  # rev["post_type"]=="meta_event":
#         continue
