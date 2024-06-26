'''用于连接bot'''

import socket
import json
import sys
import requests

from main import ctrlc_decorator

# https://blog.csdn.net/qq_27694835/article/details/108613607
# Requests 模块 https://www.cnblogs.com/saneri/p/9870901.html

try:
    i = sys.argv[1:].index('-q')
    post_port = sys.argv[1:][i+1]
except:
    post_port = '5700'
try:
    i = sys.argv[1:].index('-p')
    listen_port = sys.argv[1:][i+1]
except:
    listen_port = '5701'


url = f'http://127.0.0.1:{post_port}'
listen = ('127.0.0.1', int(listen_port))

def call_api(action: str, **params) -> dict:
    headers = {
        'Content-Type': 'application/json'
    }
    re = requests.post(
        url+f'/{action}', headers=headers, json=params, verify=False)
    try:
        return json.loads(re.text)
    except:
        return {
            'retcode':400,
            'wording':re.text,
        }


def send_msg(msg: str, user_id: int | str = None, group_id: int | str = None, **params) -> dict:
    '''user_id或者group_id是必须的'''
    if user_id is None and group_id is None:
        raise Exception('至少输入一个id!')
    return call_api('send_msg', message=msg, user_id=user_id, group_id=group_id, **params)


# 以下copy自https://zhuanlan.zhihu.com/p/404342876

ListenSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# 参考http://www.codebaoku.com/it-python/it-python-236394.html
# 以及https://blog.csdn.net/rlenew/article/details/107592753
# 这个SO_REUSEADDR是允许重用本地地址和端口
ListenSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ListenSocket.bind(listen)
ListenSocket.listen(100)  # 传入的参数指定等待连接的最大数量

HttpResponseHeader = '''HTTP/1.1 200 OK\r\n
Content-Type: text/html\r\n\r\n
'''


def request_to_json(msg: str) -> dict | None:
    '''遍历request字符串, 直到后面是json格式, 读取对应json文本并返回'''
    for i in range(len(msg)):
        if msg[i] == "{" and msg[i-1] == "\n":
            return json.loads(msg[i:])
    return None

# 需要循环执行，返回值为json格式


@ctrlc_decorator(lambda:requests.post(f'http://127.0.0.1:{listen_port}',data={}))
def recv_msg() -> dict | None:
    Client, _ = ListenSocket.accept()
    Request = Client.recv(8192).decode(encoding='utf-8')
    #print(Request)
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
