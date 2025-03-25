'''用于连接bot'''

import socket
import json
import sys
import requests
import time

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


def request_to_json(msg: str) -> dict | None:
    """
    从分块传输的HTTP请求中提取并解析JSON数据
    
    处理格式如：
    [HTTP Headers]
    
    c4
    {"json": "data"}
    0
    
    Args:
        msg (str): HTTP请求消息字符串

    Returns:
        Optional[dict]: 解析后的JSON对象，如果没有找到JSON则返回None
    """
    try:
        # 查找消息体开始的位置（双换行符后）
        body_start = msg.find('\r\n\r\n')
        if body_start == -1:
            body_start = msg.find('\n\n')
        
        if body_start == -1:
            return None
            
        # 获取消息体
        body = msg[body_start:].strip()
        
        # 查找第一个 { 和最后一个 }
        json_start = body.find('{')
        json_end = body.rfind('}')
        
        if json_start == -1 or json_end == -1:
            return None
            
        # 提取JSON字符串并解析
        json_str = body[json_start:json_end + 1]
        return json.loads(json_str)
        
    except json.JSONDecodeError:
        return None


def recv_full_message(client: socket, buffer_size=8192, max_size=1024*1024, timeout=5) ->str:
    """
    从客户端接收完整消息

    Args:
        client (socket.socket): 客户端socket对象

    Returns:
        str: 接收到的完整消息

    Raises:
        ValueError: 如果消息过大
        socket.timeout: 如果接收超时
    """
    client.settimeout(timeout)
    full_msg = b''
    start_time = time.time()

    try:
        while True:
            part = client.recv(buffer_size)
            full_msg += part

            if len(full_msg) > max_size:
                raise ValueError(f"Message too large: {len(full_msg)} bytes")

            if len(part) < buffer_size:
                break

            if time.time() - start_time > timeout:
                raise socket.timeout("Timeout while receiving message")

    except socket.timeout:
        if not full_msg:
            raise  # 如果完全没有接收到数据，则抛出超时异常

    finally:
        client.settimeout(None)  # 恢复到阻塞模式

    return full_msg.decode(encoding='utf-8', errors='ignore')

HttpResponseHeader = '''HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n'''.encode(encoding='utf-8')

# 需要循环执行，返回值为json格式
import traceback
@ctrlc_decorator(lambda:requests.post(f'http://127.0.0.1:{listen_port}',data={}))
def recv_msg() -> dict | None:
    """
    接收消息并解析为JSON

    Returns:
        Optional[Dict]: 解析后的JSON对象，如果解析失败则返回None
    """
    res = None
    with ListenSocket.accept()[0] as client:
        try:
            Request = recv_full_message(client)
            res = request_to_json(Request)
            # 发送信号表示我收到了
            client.sendall(HttpResponseHeader)
        except ValueError:
            traceback.print_exc()
            print('消息错误')
        except socket.timeout:
            print('接收超时')
        except BrokenPipeError:
            print('BrokenPipeError')
        except Exception as e:
            print(f"接收信息时发生错误: {e}")

    return res


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
