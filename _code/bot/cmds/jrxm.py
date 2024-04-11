from main import headshot, is_msg, cache, cq, getname
def run(body: str):
    '''今日小猫都是谁呢？
格式:
.jrxm'''
    qq = cache.thismsg()['user_id']
    sender = getname()
    return f'哈！今日小猫就是——\n{sender}！\n{headshot(qq)}\n你好这位小猫~'
