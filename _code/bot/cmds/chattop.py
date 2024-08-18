'''打印gpt使用计数'''
from datetime import datetime

from main import storage, getname, cache

last_call:dict = storage.get('usage', 'last_call')
last_call.setdefault('last_call', '')

multi  = 1
def run(body:str):
    '''格式:
.chattop[ <月份:1..12>]'''
    body = body.strip()
    if not body:
        body = f'{datetime.today().month}'
    try:
        month = int(body)
    except:
        return run.__doc__
    if not 1<=month<=12:
        return run.__doc__

    usage:dict = storage.get('usage', body)

    # 如果上次调用不是这个月的，表明是新的时间，清空当前月份
    this_mon = f'{datetime.today().year}-{datetime.today().month}'
    if last_call['last_call']!= this_mon:
        usage.clear()
    last_call['last_call'] = this_mon

    msg = cache.thismsg()
    if 'group_id' in msg:
        return _group(msg['group_id'], usage)
    elif msg['user_id'] in cache.ops:
        return _op(usage)
    else:
        return _user(msg['user_id'], usage)


def _group(group_id:int, usage):
    total_price = 0
    prices = []
    cache.refresh_group_member_list(group_id)
    for qq, (call_count, cost) in sorted(usage.items(), key=lambda x:x[1][1], reverse=True):
        qq = int(qq)
        if cache.in_group(qq, group_id, refresh=False):
            price = multi*cost
            total_price += price
            prices.append(f'{getname(qq)}({qq}): {call_count} 次调用, 共 ￥{price:.4f}')

    if not prices:
        return '这个月没有使用记录'
    else:
        return f'总费用:￥{total_price:.4f}\n'+'\n'.join(prices)

def _user(user_id:int, usage):
    for qq, (call_count, cost) in sorted(usage.items(), key=lambda x:x[1][1], reverse=True):
        qq = int(qq)
        if qq == user_id:
            price = multi*cost
            return f'{getname(qq)}({qq}): {call_count} 次调用, 共 ￥{price:.4f}'
    return '这个月没有使用记录'

def _op(usage):
    total_price = 0
    prices = []
    for qq, (call_count, cost) in sorted(usage.items(), key=lambda x:x[1][1], reverse=True):
        qq = int(qq)
        price = multi*cost
        total_price += price
        prices.append(f'{getname(qq)}({qq}): {call_count} 次调用, 共 ￥{price:.4f}')

    if not prices:
        return '这个月没有使用记录'
    else:
        return f'总费用:￥{total_price:.4f}\n'+'\n'.join(prices)
