'''今日老婆'''
import time
from main import getgroupstorage, getstorage, memberlist, getname, headshot, getran, cache, CQ_at, storage


jrlp_settings = storage.get('jrlp', 'settings')


def run(_:str):
    '''从群友中随机抽一个作为自己今天的老婆，每个群独立
格式:
.jrlp'''
    group_id = cache.thismsg().get('group_id')
    user_id = cache.thismsg().get('user_id')
    if group_id is None:
        return '不支持私聊'
    disabled_groups = jrlp_settings.get('disabled_groups')
    if (
        not isinstance(disabled_groups, list)
        or not all(type(item) is int and item > 0 for item in disabled_groups)
    ):
        return (
            "未配置今日老婆禁用群：请设置 "
            "storage.get('jrlp', 'settings')['disabled_groups'] 为群号列表"
        )
    if group_id in disabled_groups:
        return '该群已禁用这个功能'
    date = time.strftime('%y-%m-%d')
    data = getgroupstorage()
    data.setdefault(user_id, {})
    data = data[user_id]
    if data.get('jrlp_date')!=date:
        data['jrlp_date'] = date
        member = getran([m for m in memberlist() if not m['user_id']==user_id])
        data['jrlp'] = member['user_id']
    return f'[CQ:at,qq={user_id}]今天的老婆是\n{headshot(data["jrlp"])}\n{getname(data["jrlp"])}！'
