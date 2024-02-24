import rsa
from typing import Generator
from os import urandom
from s3 import aes


def decode(recv: bytes, this_priv: rsa.PrivateKey, is_str=True)->str|bytes:
    key_iv_bytes, message_bytes = recv[:128], recv[128:]
    key_iv = rsa.decrypt(key_iv_bytes, this_priv)
    key, iv = key_iv[:16], key_iv[16:]
    message = aes.decrypt(message_bytes, key, iv, is_str)
    return message


def encode(result: str|bytes, that_pub: rsa.PublicKey, is_str=True)->bytes:
    if is_str or not isinstance(result, bytes):
        result = str(result).encode()
    key, iv = urandom(16), urandom(16)
    message_bytes = aes.encrypt(result, key, iv, is_str=False)
    key_iv_bytes = rsa.encrypt(key+iv, that_pub)
    return key_iv_bytes+message_bytes


class VerifyError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


def msg(text:str):
    s = ''
    lines = text.splitlines()
    for i in range(len(lines)):
        pre = ''
        if i>0:
            pre = '\n'
        if i==len(lines)-1:
            s += pre+'\\'+lines[i]
        else:
            s += pre+'|'+lines[i]
    return s