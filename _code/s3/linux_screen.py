
import time,os

from main import file, str_tool

def _logpath(name):
    return f'data/screens/{name}/screenlog.0'

def rel_exec(path, command):
    os.system(f'cd {path} && {command} && cd -')

def start(name):
    dirpath = f'data/screens/{name}'
    os.makedirs(dirpath,exist_ok=True)
    rel_exec(dirpath, f'screen -L -dmS {name}')

def send(name, command):
    logpath = _logpath(name)
    file.write(logpath, '')
    os.system(f"screen -S {name} -X stuff $'{command}\n'")
    s=''
    while s=='':
        s = file.read(logpath)
        time.sleep(0.2)
    return str_tool.remove_emptyline(s)

def pop_log(name):
    '''这个可能会获取过长的字符串，经过处理再发送消息比较好'''
    logpath = _logpath(name)
    text = file.read(logpath)
    file.write(logpath, '')
    return text


def log(name, start=None, end=None):
    logpath = _logpath(name)
    text = file.read(logpath)
    return str_tool.slice(text,start, end)

def stop(name):
    logpath = _logpath(name)
    file.write(logpath, '')
    return os.system(f'screen -S {name} -X quit')