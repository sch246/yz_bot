'''重启'''
import os

from main import file, send, cache


def run(body:str):
    msg = cache.get_last()
    if not msg['user_id'] in cache.get_ops():
        if not cache.any_same(msg, '\.reboot'):
            return '权限不足(一定消息内将不再提醒)'
        return
    if body.strip()=='':
        send('重启中', **msg)
        file.write(file.ensure_file('data/reboot_greet.py'), f"send('重启完成',**{msg})")
        exit(233)

# 用于重启时打招呼
# def load():
reboot_greet = file.ensure_file('data/reboot_greet.py')
if os.path.isfile(reboot_greet):
    exec(open(reboot_greet,encoding='utf-8').read())
    os.remove(reboot_greet)