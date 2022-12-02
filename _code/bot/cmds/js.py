'''JavaScript!'''

import os
from main import cache, cq, screen, str_tool

def run(body:str):
    msg = cache.get_last()
    body = cq.unescape(body.strip())

    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.py'):
            return '权限不足(一定消息内将不再提醒)'

    flag, value = ensure()
    if not flag:
        return value

    return str_tool.stripline(screen.send('js',f'{body}\n'))


def ensure():
    '''保证可以运行，并且保证运行的是js'''
    def err(name, info):
        return name+' 错误:\n'+info
    if not screen.check():
        return False, err('screen',os.popen('screen -v').read())
    if not screen.check('js'):
        screen.start('js')
    if not screen.send('js','1').endswith('> '):
        s = screen.send('js','node')
        if 'Type ".help"' not in s:
            return False, err('node',str_tool.stripline(s))
    return True, None
