'''管理bot的命令，该文件夹下的文件都是命令'''

import os
import importlib
import re

commands = []
msg = {}
modules = {}

for name in os.listdir(os.path.split(__file__)[0]):
    if name.startswith('_'):
        continue
    if name.endswith('.py'):
        name = name[:-3]
        commands.append(name)


# print(commands)
_match_S = re.compile(r'\S')


def run(text:str):
    for command in commands:
        if text.startswith(command):

            body = text[len(command):]
            if re.match(_match_S, body):  # 如果命令后跟的是非空白符，则表示不是这个命令
                continue
            body = body.lstrip()  # body是命令后面的其余部分

            return modules[command].run(body)

def load():
    for command in commands:
        modules[command] = importlib.import_module('bot.cmds.'+command)
