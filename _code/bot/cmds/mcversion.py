'''获取mc版本并推送到订阅的群'''

import time
import requests

from main import to_thread, storage, send
from . import params

ver:dict = storage.get('mcversion','ver')
ver.setdefault('snapshot','')
subscribes:list = storage.get('mcversion','subscribes',list)

@params
def run(msg, arg0, last, last_lines):
    '''用于查询我的世界版本，也可以订阅提醒
格式:
.mcversion         # 查询最新版本
.mcversion enable  # 启用订阅
.mcversion disable # 禁用订阅
    '''
    if not arg0:
        latest:dict = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()['latest']
        return f"当前最新版本是{latest['release']}\n快照为{latest['snapshot']}"

    sub = {
        'group_id': msg.get('group_id'),
        'user_id': msg.get('user_id'),
    }

    if arg0=='enable':
        subscribes.append(sub)
        return '已订阅更新提醒'
    elif arg0=='disable':
        subscribes.remove(sub)
        return '已取消订阅'

    return run.__doc__


def loop():
    while True:
        latest:dict = requests.get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()['latest']
        # for sub in subscribes:
        #     send(f"测试消息", **sub)
        if ver['snapshot'] != latest['snapshot']:
            ver['release'] = latest['release']
            ver['snapshot'] = latest['snapshot']
            for sub in subscribes:
                send(f"发现新版本:{ver['snapshot']}({ver['release']})", **sub)
        time.sleep(60)

thread = to_thread(loop, True)()
