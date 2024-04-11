from main import headshot, is_msg, cache, cq
def run(body: str):
    '''测试
格式:
.pctest'''
    qq = cache.thismsg()['user_id']
    return f'头像为：{headshot(qq)}'
