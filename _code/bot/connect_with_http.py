'''用于连接bot'''

import socket
import json
import sys
import requests
import time
import re

from main import ctrlc_decorator

# 这些群/私聊的消息将转发到对应端口而不是默认
post_map = {
}

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


post_url = f'http://127.0.0.1'
listen = ('127.0.0.1', int(listen_port))
REQUEST_TIMEOUT = 5  # 5秒超时

def call_api(action: str, **params) -> dict:
    port = post_port
    if 'group_id' in params:
        group_key = f'g{params["group_id"]}'
        port = post_map.get(group_key, post_port)
    if 'user_id' in params:
        user_key = f'u{params["user_id"]}'
        port = post_map.get(user_key, post_port)
    headers = {
        'Content-Type': 'application/json'
    }
    re = requests.post(
        post_url+f':{port}/{action}',
        headers=headers,
        json=params,
        verify=False,
        timeout=REQUEST_TIMEOUT  # 添加超时
        )
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
    从完整的HTTP请求中提取并解析JSON数据。
    这个版本更通用，能处理两种类型的请求。
    """
    try:
        # 查找消息体开始的位置（双换行符后）
        body_start = msg.find('\r\n\r\n')
        if body_start == -1:
            return None
            
        # 获取消息体
        body = msg[body_start:].strip()
        
        # 尝试直接解析，这适用于Content-Length的情况
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            # 如果直接解析失败，可能是chunked编码，其格式为
            # [hex_len]\r\n{json_data}\r\n0\r\n\r\n
            # 我们直接在body里找第一个 { 和最后一个 }
            json_start = body.find('{')
            json_end = body.rfind('}')
            
            if json_start != -1 and json_end != -1:
                json_str = body[json_start:json_end + 1]
                return json.loads(json_str)
            else:
                return None
                
    except Exception:
        # 捕获所有可能的解析错误
        return None


def recv_full_message(client: socket.socket, buffer_size=4092) -> str:
    """
    从客户端接收完整的HTTP消息（支持Content-Length和Chunked编码）。

    Args:
        client (socket.socket): 客户端socket对象.
        buffer_size (int): 每次读取的缓冲区大小.

    Returns:
        str: 接收到的完整消息字符串.
    """
    client.settimeout(5.0)  # 设置一个整体的超时
    
    # 1. 先接收HTTP头部
    raw_request = b''
    while b'\r\n\r\n' not in raw_request:
        try:
            part = client.recv(buffer_size)
            if not part:
                break # 连接已关闭
            raw_request += part
        except socket.timeout:
            print("接收HTTP头部超时")
            return "" # 返回空字符串表示失败

    # 将字节解码为字符串，仅处理头部部分以避免解码错误
    headers_part = raw_request.split(b'\r\n\r\n', 1)[0].decode('utf-8', errors='ignore')
    
    # 2. 检查 Content-Length
    content_length_match = re.search(r'Content-Length: (\d+)', headers_part, re.IGNORECASE)
    
    if content_length_match:
        # --- 方案A: 存在 Content-Length ---
        content_length = int(content_length_match.group(1))
        
        # 从 raw_request 中分离出已经接收到的 body 部分
        header_bytes, body_received = raw_request.split(b'\r\n\r\n', 1)
        
        # 计算还需要接收多少字节
        remaining_bytes = content_length - len(body_received)
        
        # 循环接收直到满足 Content-Length
        while remaining_bytes > 0:
            try:
                part = client.recv(min(remaining_bytes, buffer_size))
                if not part:
                    break # 连接异常关闭
                body_received += part
                remaining_bytes -= len(part)
            except socket.timeout:
                print(f"接收消息体超时，已接收 {len(body_received)}/{content_length} 字节")
                break # 超时退出
        
        return (header_bytes + b'\r\n\r\n' + body_received).decode('utf-8', errors='ignore')

    else:
        # --- 方案B: 不存在 Content-Length (假定为 Chunked 或简单连接) ---
        # 我们已经接收了头部和可能的第一部分数据，继续接收直到超时或收到空数据
        full_msg_bytes = raw_request
        try:
            while True:
                # 在非阻塞模式下尝试读取，直到没有更多数据
                client.settimeout(0.2) # 短暂超时，用于接收分块数据
                part = client.recv(buffer_size)
                if not part:
                    break # 这是chunked编码的正常结束方式之一
                full_msg_bytes += part
        except socket.timeout:
            # 超时在这里是正常的，表示数据流结束
            pass
        finally:
            client.settimeout(None) # 恢复阻塞模式
            
        return full_msg_bytes.decode('utf-8', errors='ignore')

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
            if not Request:
                print("未接收到有效请求")
                return None
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
