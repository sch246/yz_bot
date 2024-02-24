'''kemomimi!
使用的api:https://api.moedog.org/pixiv/v2.html'''
from random import randint
import re
import time
from main import cache, getran, jcurl, read_params, cq, to_thread,sendmsg

def run(body:str):
    '''随机返回一张kemomimi酱作者的图片(似乎已经用不了了)
    格式: .kmmm [<page:Int>]'''
    thread(body)

@to_thread
def thread(body:str):
    cache.thismsg(cache.get_last())
    param, body = read_params(body)
    try:
        works = False
        for i in range(5):
            works = list(get_works(int(param) if param else None))
            if works: break
            if i==4:
                return sendmsg('循环5次后没有随机到kmmm')
            time.sleep(0.5)
    except:
        return sendmsg(run.__doc__)
    pics = []
    for work in works:
        pics.extend(get_pics(work))
    medium, original = getran(pics)
    sendmsg(cq.url2cq(medium)+original)
    # sendmsg(original)

def get_works(page=None):
    if page is not None:
        works =  jcurl(f'https://api.moedog.org/pixiv/v2/?type=member_illust&id=2509595&page={page}')['illusts']
    else:
        works = False
    count = 0
    while not works:
        count+=1
        page = randint(1,26)
        works =  jcurl(f'https://api.moedog.org/pixiv/v2/?type=member_illust&id=2509595&page={page}')['illusts']
        if not works:
            if count>=3:
                return sendmsg('获取失败')
            time.sleep(0.5)
            sendmsg(f'第 {count+1} 次获取中')
    return filter(lambda work: any(
            tag['translated_name']=='Kemomimi-chan'
            for tag in work['tags']),
        works)


def get_pics(work):
    pics = []
    if work['meta_pages']:# 如果是多个图片的作品
        for p in work['meta_pages']:
            urls = p['image_urls']
            medium = urls['medium'].replace('i.pximg.net','i.pixiv.re')
            original = urls['original']
            def mt(m:re.Match):
                uid, p, ty = m.groups()
                return f'pixiv.re/{uid}-{int(p)+1}.{ty}'
            original = re.sub(
                    r'https://.+/(\d+)_p(\d+).(jpg|png|gif)',
                    mt,
                    original)
            pics.append((medium, original))
    else:
        urls = work['image_urls']
        medium = urls['medium'].replace('i.pximg.net','i.pixiv.re')
        original = urls.get('original') or work['meta_single_page']['original_image_url']
        original = re.sub(
                r'https://.+/(\d+)_p(\d+).(jpg|png|gif)',
                'pixiv.re/\g<1>.\g<3>',
                original)
        pics.append((medium, original))
    return pics
