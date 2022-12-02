'''Nim!'''

import os
from main import cache, cq, file

def run(body:str):
    msg = cache.get_last()
    body = cq.unescape(body.strip())

    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.py'):
            return '权限不足(一定消息内将不再提醒)'

    flag, value = ensure()
    if not flag:
        return value

    file.write('data/tmp.nim',body)
    return os.popen('nim compile --verbosity:0 --hints:off --run "data/tmp.nim"').read()


def ensure():
    '''保证有nim'''
    def err(name, info):
        return name+' 错误:\n'+info
    s =os.popen('nim -v').read()
    if 'Nim Compiler' not in s:
        return False, err('node', s)
    return True, None

