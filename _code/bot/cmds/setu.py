'''色图
使用的api:https://api.lolicon.app/#/setu'''
import re
from main import jcurl, cq,sendmsg

def run(body:str):
    '''随机返回一张色图，R18的只会返回链接
    格式: .setu [<params>]'''
    params = list(map(lambda s:s.strip(),body.strip().split('\n')))
    pics= jcurl(f'https://api.lolicon.app/setu/v2?size=original&size=regular&{"&".join(params)}').get('data')
    if not pics: return '获取失败'
    pic = pics[0]
    urls = pic['urls']
    original = urls['original']
    original = re.sub(
            r'https://.+/(\d+)_p(\d+).(jpg|png|gif)',
            'pixiv.re/\g<1>.\g<3>',
            original)
    sendmsg(original)
    sendmsg(cq.url2cq(urls['regular'].replace('i.pximg.net','i.pixiv.re')))
