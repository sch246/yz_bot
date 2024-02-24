'''newlisp!'''

import os
from main import cache, cq, screen

def run(body:str):
    '''运行newlisp代码，仅在linux上有效
格式:
.nl <Code>'''
    msg = cache.thismsg()
    body = cq.unescape(body.strip())
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, '\.nl'):
            return '权限不足(一定消息内将不再提醒)'

    if not screen.check():
        return f'screen错误:{os.popen("screen -v").read()}'
    if not screen.check('newlisp'):
        screen.start('newlisp')
    if not screen.send('newlisp','1\n').strip().endswith('>'):
        s = screen.send('newlisp','newlisp')
        if 'newlisp -h' not in s:
            return f'newlisp错误:{s}'
    return screen.send('newlisp',body+'\n').strip()
