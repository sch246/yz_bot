import socket
import getpass
import rsa
from os import makedirs
from os.path import join
from s3.rsa import pub_dumps, priv_dumps


def info():
    return f'{getpass.getuser()}@{socket.gethostname()}'

def genkeys(name:str, path:str='./'):
    if ' ' in name or '\n' in name or '\r' in name or '\t' in name:
        raise ValueError('name中不能含有空白符')
    pub, priv = rsa.newkeys(1024)
    makedirs(path,exist_ok=True)
    with open(join(path,'rsa.priv'),'w',encoding='utf-8') as f:
        f.write(f'{name}\n{priv_dumps(priv)}')
    with open(join(path,'rsa.pub'),'w',encoding='utf-8') as f:
        f.write(f'{name} {pub_dumps(pub)}\n')