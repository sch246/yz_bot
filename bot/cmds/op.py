'''设定管理员，应当编辑并reload此文件以应用更改'''
import re
from . import msg
from bot.cache import get_ops, add_op, _add_op

_match_at = re.compile(r'\[CQ:at,qq=([0-9]+)\]$')
_match_qq = re.compile(r'[0-9]+$')

def add(uid):
    add_op(uid)

def run(body:str):
    if not msg['user_id'] in get_ops():
        return
    body = body.strip()
    if body=='':
        return
    fail = []
    success = []
    for line in body.splitlines():
        for uid in line.split(' '):
            uid = uid.strip()
            is_CQ = re.match(_match_at, uid)
            is_qq = re.match(_match_qq, uid)
            if not is_CQ and not is_qq:
                fail.append(uid)
            else:
                if is_CQ:
                    uid = int(is_CQ.group(1))
                elif is_qq:
                    uid = int(uid)
                _add_op(uid)
                success.append(uid)
    open(__file__,'a',encoding='utf-8').writelines(map(lambda s:f'\nadd({s})', success))
    return f'执行完毕,成功{len(success)}个，失败{len(fail)}个'
