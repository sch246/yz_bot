'''管理bot的命令，该文件夹下的文件都是命令'''

import os
import importlib
import re
from inspect import signature
from functools import wraps


commands = []
msg = {}
modules = {}
fails = []


for name in os.listdir(os.path.split(__file__)[0]):
    if name.startswith('_'):
        continue
    if name.endswith('.py'):
        name = name[:-3]
        commands.append(name)


# print(commands)
_match_S = re.compile(r'\S')

def is_cmd(text:str):
    for command in commands:
        if text.startswith(command):
            body = text[len(command):]
            if re.match(_match_S, body):  # 如果命令后跟的是非空白符，则表示不是这个命令
                continue
            # body = body.lstrip()  # body是命令后面的其余部分
            return command, body

def run(command, body):
    return modules[command].run(body)

def load():
    from main import file, send
    
    if os.path.isfile(file.ensure_file('data/reboot_greet.py')):
        msg = file.json_read('data/reboot_greet.py')

    for command in commands:
        try:
            modules[command] = importlib.import_module('bot.cmds.'+command)
        except:
            fails.append(command)

    if fails:
        send(f'加载失败: {fails}', **msg)



def params(f):
    from main import cache, cq, read_params
    @wraps(f)
    def wrapper(body:str):
        msg = cache.thismsg()

        body = cq.unescape(body)
        lines = body.splitlines()
        while len(lines)<2:
            lines.append('')
        last, *last_lines = lines

        num_args = len(signature(f).parameters)
        args = [msg]

        while len(args) < num_args-2:
            s, last = read_params(last)
            args.append(s)

        return f(*args, last, last_lines)
    return wrapper

def grouponly(f):
    from main import cache
    @wraps(f)
    def wrapper(*args,**kws):
        group_id = cache.thismsg().get('group_id')
        if group_id is None:
            return '此命令仅群内可用!'
        return f(*args,**kws)
    return wrapper