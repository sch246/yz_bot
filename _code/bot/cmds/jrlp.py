'''今日老婆'''
import time
from main import getgroupstorage, memberlist, getname, headshot, getran, cache


def run(_:str):
    '''从群友中随机抽一个作为大家今天的老婆，群之间是独立的
注：鸽子院禁用了该功能
格式:
.jrlp'''
    group_id = cache.thismsg().get('group_id')
    if group_id is None:
        return '不支持私聊'
    if group_id == 916083933:
        return
    date = time.strftime('%y-%m-%d')
    data = getgroupstorage()
    if data.get('jrlp_date')!=date:
        data['jrlp_date'] = date
        member = getran(memberlist())
        data['jrlp'] = member['user_id']
    return f'今天的群友老婆是\n{headshot(data["jrlp"])}\n{getname(data["jrlp"])}\n恭喜这位群友！'
