'''今日鸽子'''
import time
from main import getgroupstorage, memberlist, getname, headshot, getran


def run(_:str):
    date = time.strftime('%y-%m-%d')
    data = getgroupstorage()
    if data.get('jrgz_date')!=date:
        data['jrgz_date'] = date
        member = getran(memberlist())
        data['jrgz'] = member['user_id']
    return f'今日鸽子（1/1）\n{getname(data["jrgz"])}\n{headshot(data["jrgz"])}\n恭喜这位鸽子，今天你可以光明正大的咕咕咕啦！'
