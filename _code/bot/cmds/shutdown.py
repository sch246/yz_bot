'''关闭'''
import os

from main import file, send, cache



def run(body:str):
    msg = cache.get_last()
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, r'\.shutdown'):
            return '权限不足(一定消息内将不再提醒)'
        return
    if body.strip()=='':
        send('关闭中', **msg)
        file.write(file.ensure_file('data/shutdown_greet.py'), f"send('{cache.get_nickname()}醒了！',**{msg})")
        exit(0)


'''用于在启动时发出醒了的声音'''
# def load():
shutdown_greet = file.ensure_file('data/shutdown_greet.py')
if os.path.isfile(shutdown_greet):
    exec(open(shutdown_greet,encoding='utf-8').read())
    os.remove(shutdown_greet)
