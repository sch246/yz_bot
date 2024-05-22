'''它来自哪里呢？'''
from main import headshot, is_msg, cache, cq
def run(body: str):
    '''什么？为什么会出现这条命令？
它不属于柚子的创建者，似乎来自另一个也许你所不知的神秘存在。
——你永远猜不中它的内容哪天会变成什么。（
格式：
.pctest'''
    qq = cache.thismsg()['user_id']   
    return f'测试完毕，测试者为：{headshot(qq)}'   