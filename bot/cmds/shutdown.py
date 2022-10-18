'''关闭'''
import os
from bot import send
from s3.file import write, ensure_file
from . import msg
from bot.cache import get_ops, get_nickname



def run(body:str):
    if not msg['user_id'] in get_ops():
        return
    if body.strip()=='':
        send('关闭中', **msg)
        write(ensure_file('data/shutdown_greet.py'), f"send('{get_nickname()}醒了！',**{msg})")
        exit(0)


'''用于在启动时发出醒了的声音'''
# def load():
shutdown_greet = 'data/shutdown_greet.py'
if os.path.isfile(shutdown_greet):
    exec(open(shutdown_greet,encoding='utf-8').read())
    os.remove(shutdown_greet)
