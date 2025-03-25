'''jm!'''
import jmcomic
import os
from main import cache, cq, read_params, sendmsg

def run(body:str):
    '''禁漫获取
格式:
.jm <book_id:int>'''
    msg = cache.thismsg()
    body = cq.unescape(body)
    arg, last = read_params(body)

    if arg.isdigit():
        book_id = int(arg)
        option = jmcomic.create_option_by_file('data/option.yml')
        option.download_album(book_id)

        path = os.path.abspath(f'data/jm/{book_id}.pdf')
        if not os.path.isfile(path):
            return f'下载失败，文件"{path}"不存在'
        sendmsg(cq.dump({
            'type':'file',
            'data':{
                'file':f'file://{path}'
            }
        }))
        return cq.dump({
            'type':'reply',
            'data':{
                'id': msg['message_id']
            }
        }) + '你的本子已下载并转换为PDF，已发送给你！'
    return run.__doc__
