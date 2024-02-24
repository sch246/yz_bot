import socket
import rsa
from typing import Callable, NoReturn

from s3.thread import to_thread
from s3.rsa import pub_loads

from . import decode, encode, VerifyError, msg


def load_pubkeys(path: str) -> dict:
    d = {}
    with open(path, encoding='utf-8') as f:
        for line in f.readlines():
            line = line.strip()
            if line:
                user, keytext = line.split(' ')
                d[user] = pub_loads(keytext)
    return d


def listen(ip: str, port: int, pubkeys: dict, count: int = 100, is_str=True):
    def wrapper(func: Callable[[str], str]) -> Callable[[], NoReturn]:
        def loop():
            # 服务器保存一堆公钥
            # 每次启动时，创建一对新的rsa秘钥
            this_pub, this_priv = rsa.newkeys(1024)
            listen_socket = _bind(ip, port, count)
            print('已启动')
            _waiting(listen_socket, pubkeys, this_pub, this_priv, func, is_str)
            _cmd(func, is_str)
        return loop
    return wrapper


def _bind(ip: str, port: int, count: int):
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 这个SO_REUSEADDR是允许重用本地地址和端口
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind((ip, port))
    listen_socket.listen(count)  # 传入的参数指定等待连接的最大数量
    return listen_socket


def _cmd(func, is_str):
    '''交互式'''
    while True:
        text = input()
        if not is_str:
            continue
        if text.startswith('/'):
            print(msg(str(func(text[1:]))))
        elif text=='stop':
            exit()

@to_thread
def _waiting(listen_socket, pubkeys, this_pub, this_priv, func, is_str:bool)->None:
    while True:
        # 持续等待连接
        sk, _ = listen_socket.accept()
        try:
            connect(sk, is_str, pubkeys, this_pub, this_priv, func).start()
        except VerifyError as e:
            print(f'{e.args[0]}: 验证错误')
        except KeyError as e:
            print(f'{e.args[0]}: 未知的客户端')



class connect:
    def __init__(self ,sk:socket.socket, is_str:bool, pubkeys:dict[str,rsa.PublicKey],
            this_pub:rsa.PublicKey, this_priv:rsa.PrivateKey, func:Callable[[str|bytes], str|bytes]) -> None:
        # 首先进行身份认证
        ## 接收签名及名字, 长度为128的前提是使用1024位的rsa
        recv0 = sk.recv(8192)
        sign, ran0, name = recv0[:128], recv0[128:144], recv0[144:]
        name = name.decode()
        try:
            that_pub = pubkeys[name]
        except KeyError:
            sk.send(b'0')
            sk.close()
            raise KeyError(name)
        try:
            rsa.verify(ran0, sign, that_pub)
        except rsa.VerificationError:
            sk.send(b'1')
            sk.close()
            raise VerifyError(name)
        # 发送自己的公钥
        sk.send(encode(this_pub.save_pkcs1(), that_pub, is_str=False))

        self.sk = sk
        self.name = name
        self.is_str = is_str
        self.this_priv = this_priv
        self.that_pub = that_pub
        self.func = func
    @to_thread
    def start(self):
        print(f'{self.name}: 通信开始')
        while True:
            # 对于每个连接，循环消息
            try:
                r = self.sk.recv(8192)
                if r==b'':
                    print(f'{self.name}: 通信结束')
                    return
                message = decode(r, self.this_priv, self.is_str)
                print(f'{self.name} > {repr(message)}')
                result = self.func(message)
                self.sk.send(encode(result, self.that_pub, self.is_str))
                print(f'{self.name} < {repr(result)}')
            except BrokenPipeError:
                return

