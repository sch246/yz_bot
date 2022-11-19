'''设定管理员'''
from itertools import chain
import re

from main import Show, cache

_match_at = re.compile(r'\[CQ:at,qq=([0-9]+)\]$')
_match_qq = re.compile(r'[0-9]+$')

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
    msg = cache.get_last()
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, r'\.op'):
            return '权限不足(一定消息内将不再提醒)'
        return
    body = body.strip()
    if body=='':
        return run.__doc__
    if body.startswith('del'):
        body = body[3:]
        success, fail = [], []
        uids = get_uids_from_body(body)
        for uid in uids:
            uid = get_uid(uid)
            if uid == cache.ops[0]:
                fail.append(Show(f'{uid}:不能移除master'))
            elif uid in cache.ops:
                cache.ops.remove(uid)
                success.append(uid)
            else:
                fail.append(Show(f'{uid}:不是op'))
        cache.ops_save()
        return f'执行完毕,成功:{success}，失败:{fail}'
    else:
        uids = get_uids_from_body(body)
        success, fail = [], []
        for uid in uids:
            uid = get_uid(uid)
            if uid:
                if not uid in cache.ops:
                    cache.ops.append(uid)
                    success.append(uid)
                else:
                    fail.append(Show(f'{uid}:已是op'))
            else:
                fail.append(Show(f'{uid}:格式错误'))
        cache.ops_save()
        return f'执行完毕,成功:{success}，失败:{fail}'
