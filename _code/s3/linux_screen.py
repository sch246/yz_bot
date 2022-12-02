
import time,os

from main import file, str_tool

def _logpath(name):
    return f'data/screens/{name}.0'

def rel_exec(path, command):
    os.system(f'cd {path} && {command} && cd -')

def check(name=None):
    if name is not None:
        return os.popen(f'screen -ls | grep .{name}').read()
    elif 'version' in os.popen('screen -v').read():
        return True
    else:
        return False

def start(name):
    os.makedirs('data/screens',exist_ok=True)
    logpath = _logpath(name)
    os.system(f"screen -dmS {name} \
        && screen -S {name} -X stuff $'exec 1>> {logpath}\n' \
        && screen -S {name} -X stuff $'exec 2>> {logpath}\n'")

def send(name, command):
    logpath = _logpath(name)
    file.write(logpath, '')
    command = command.replace('"','\\"')
    os.system(f'''screen -S {name} -X stuff "{command}\n"''')
    # s=''
    # while s=='':
    #     s = file.read(logpath)
    #     time.sleep(0.2)
    time.sleep(0.2)
    return pop(name)

def pop(name):
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