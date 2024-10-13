'''今日老婆'''
import time
from main import getgroupstorage, getstorage, memberlist, getname, headshot, getran, cache, CQ_at


def run(_:str):
    '''从群友中随机抽一个作为自己今天的老婆，每个群独立
注：鸽子院禁用了该功能
格式:
.jrlp'''
    group_id = cache.thismsg().get('group_id')
    user_id = cache.thismsg().get('user_id')
    if group_id is None:
        return '不支持私聊'
    if group_id == 916083933:
        return '应雨弓弓的要求禁用了这个功能'
    date = time.strftime('%y-%m-%d')
    data = getgroupstorage()
    data.setdefault(user_id, {})
    data = data[user_id]
    if data.get('jrlp_date')!=date:
        data['jrlp_date'] = date
        member = getran([m for m in memberlist() if not m['user_id']==user_id])
        data['jrlp'] = member['user_id']
    return f'[CQ:at,qq={user_id}]今天的老婆是\n{headshot(data["jrlp"])}\n{getname(data["jrlp"])}！'
