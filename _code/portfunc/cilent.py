import socket
import rsa
from os import urandom
from . import encode, decode, VerifyError

from s3.rsa import priv_loads

def load_this_priv(path: str) -> rsa.PrivateKey:
    with open(path, encoding='utf-8') as f:
        name, *key_lines = f.read().splitlines()
        return name, priv_loads('\n'.join(key_lines))


class connect:
    def __init__(self, ip: str, port: int, name_priv:tuple[str, rsa.PublicKey], is_str=True) -> None:
        self.is_str=is_str
        sk = socket.socket()
        sk.connect((ip, port))
        self.sk = sk
        self.name, self.this_priv = name_priv
        # 进行身份认证
        # 发送签名以及自己的主机名
        ran0 = urandom(16)
        sk.send(rsa.sign(ran0, self.this_priv, 'SHA-1')+ran0+self.name.encode())
        # 接收服务器发送的公钥
        that_pub_bytes = sk.recv(8192)
        if that_pub_bytes==b'0':
            raise KeyError
        elif that_pub_bytes==b'1':
            raise VerifyError
        that_pub = rsa.PublicKey.load_pkcs1(decode(that_pub_bytes, self.this_priv, is_str))
        self.that_pub = that_pub

    def call(self, text: str|bytes):
        s=encode(text, self.that_pub, self.is_str)
        if len(s)%1024==0:
            s += b'msg end'
        self.sk.sendall(s)
        r=bytes()
        while True:
            data = self.sk.recv(1024)
            r += data
            if len(data)<1024:break
        if data.endswith(b'msg end'):
            r = r[:-7]
        return decode(r,self.this_priv, self.is_str)

    def close(self):
        self.sk.close()
