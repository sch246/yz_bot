'''重启'''
import os
from s3.file import write, ensure_file
from bot import send
from . import msg
from bot.cache import get_ops

def run(body:str):
    if not msg['user_id'] in get_ops():
        return
    if body.strip()=='':
        send('重启中', **msg)
        write(ensure_file('data/reboot_greet.py'), f"send('重启完成',**{msg})")
        exit(2)

# 用于重启时打招呼
# def load():
reboot_greet = 'data/reboot_greet.py'
if os.path.isfile(reboot_greet):
    exec(open(reboot_greet,encoding='utf-8').read())
    os.remove(reboot_greet)