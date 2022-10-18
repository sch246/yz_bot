'''设定管理员，应当编辑并reload此文件以应用更改'''
from itertools import chain
import re
from s3 import Show

from s3.config import save_config
from . import msg
from bot.cache import get_ops, add_op, _add_op, del_op

_match_at = re.compile(r'\[CQ:at,qq=([0-9]+)\]$')
_match_qq = re.compile(r'[0-9]+$')

def add(uid):
    add_op(uid)

def get_uid(uid:str):
    is_CQ = re.match(_match_at, uid)
    is_qq = re.match(_match_qq, uid)
    if not is_CQ and not is_qq:
        return False
    else:
        if is_CQ:
            return int(is_CQ.group(1))
        elif is_qq:
            return int(uid)

def get_uids_from_body(s:str):
    return list(filter(lambda s:s.strip()!='', chain(*[[uid for uid in line.split(' ')] for line in s.splitlines()])))

def run(body:str):
    '''用于管理权限
    格式: .op [del] (<qq号:int> | <at某人:cq[at]>)+ 可换行
    警告: op可以给予任何人op, 或者删除任何master外的人的op, 请谨慎给予'''
    if not msg['user_id'] in get_ops():
        return
    body = body.strip()
    if body.splitlines()[0].split(' ')[0]=='del':
        body = body[3:]
        success, fail = [], []
        uids = get_uids_from_body(body)
        for uid in uids:
            uid = get_uid(uid)
            if uid == get_ops()[0]:
                fail.append(Show(f'{uid}:不能移除master'))
            elif del_op(uid):
                success.append(uid)
            else:
                fail.append(Show(f'{uid}:不是op'))
        save_config(get_ops(),'ops')
        return f'执行完毕,成功:{success}，失败:{fail}'
    if not body=='':
        uids = get_uids_from_body(body)
        success, fail = [], []
        for uid in uids:
            uid = get_uid(uid)
            if uid:
                if _add_op(uid):
                    success.append(uid)
                else:
                    fail.append(Show(f'{uid}:已是op'))
            else:
                fail.append(Show(f'{uid}:格式错误'))
        save_config(get_ops(),'ops')
        return f'执行完毕,成功:{success}，失败:{fail}'
    return run.__doc__
