'''lua!'''

import os
from main import cache, cq, file

def run(body:str):
    '''运行lua代码，仅在linux上有效
格式:
.lua <Code>'''
    msg = cache.thismsg()
    body = cq.unescape(body.strip())

    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.lua'):
            return '权限不足(一定消息内将不再提醒)'

    #flag, value = ensure()
    #if not flag:
    #    return value

    file.write('data/tmp.lua',body)
    return os.popen('/usr/bin/lua data/tmp.lua').read()


def ensure():
    '''保证有lua'''
    def err(name, info):
        return name+' 错误:\n'+info
    s =os.popen('/usr/bin/lua -v').read()
    if 'Lua.org' not in s:
        return False, err('lua', s)
    return True, None
