'''打印gpt使用计数(目前已经没用了)'''
from datetime import datetime

from main import storage, getname

# price = 0.000_002
price  = 0.000_001
def run(body:str):
    '''格式:
.chattop[ <月份:int>]'''

    body = body.strip()
    if not body:
        body = f'{datetime.today().month}'
    try:
        month = int(body)
    except:
        return run.__doc__
    if not 1<=month<=12:
        return run.__doc__
    usage=storage.get('usage', body)
    if not usage:
        return '这个月没有使用记录'
    total_price = price*sum(map(lambda val: val[1], usage.values()))
    return f'总费用:￥{total_price}\n'+'\n'.join(map(
            lambda item:f'{getname(item[0])}: {item[1][0]} 次调用, 共 ￥{price*item[1][1]}',
            sorted(usage.items(), key=lambda x:x[1][1], reverse=True)
            ))
