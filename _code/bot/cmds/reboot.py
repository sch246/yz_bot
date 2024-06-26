'''重启'''
import os

from main import file, send, cache


def run(body:str):
    '''重启
格式:
.reboot'''
    msg = cache.thismsg()
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, r'\.reboot'):
            return '权限不足(一定消息内将不再提醒)'
        return
    if body.strip()=='':
        send('重启中', **msg).result()
        file.json_write(file.ensure_file('data/reboot_greet.py'), msg)
        exit(233)

# 用于重启时打招呼
# def load():
reboot_greet = file.ensure_file('data/reboot_greet.py')
if os.path.isfile(reboot_greet):
    msg = file.json_read('data/reboot_greet.py')
    send('重启完成', **msg)
    os.remove(reboot_greet)
