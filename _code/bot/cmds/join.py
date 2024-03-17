'''用于cs选人'''
from random import choice

from main import getgroupstorage, sendmsg, getname, getran

from . import params, grouponly

def members(lst):
    return '\n'.join([f'- {getname(qq)} ({qq})' for qq in lst])

@params
@grouponly
def run(msg, arg0, arg1, last, last_lines):
    '''用于cs内战组队，所有人确认后会随机roll出一个人
格式:
.join start[ <总人数>]
.join
.join end
    '''
    loc = getgroupstorage().setdefault('join', {})

    if arg0=='start':
        try:
            size = int(arg1) if arg1 else 10
            if size < 1:
                return '总人数至少为1'
            loc['size'] = size
            loc['count'] = size
            loc['lst'] = []
            return '请参加人员依次输入.join'
        except:
            return '人数需要是整数'

    elif not arg0:
        if loc.get('size', None) is None:
            return '请先输入.join start'

        qq = msg['user_id']
        if qq in loc['lst']:
            return '已在列表中！'
        loc['count'] -= 1
        loc['lst'].append(qq)
        sendmsg(f"已加入！\n当前人数: {len(loc['lst'])} / {loc['size']}")

        if loc['count']==0:
            sendmsg(f"人数已满！\n" + members(loc['lst']))
            qq = choice(loc['lst'])
            del loc['count']
            del loc['size']
            del loc['lst']
            sendmsg(f'随机出的人选是: {getname(qq)} ({qq})！')
        return

    elif arg0=='end':
        if not loc['lst']:
            return '已取消'
        else:
            sendmsg(f"人数已定！\n" + members(loc['lst']))
            qq = choice(loc['lst'])
            sendmsg(f'随机出的人选是: {getname(qq)} ({qq})！')
        del loc['count']
        del loc['size']
        del loc['lst']

        return


    return run.__doc__
