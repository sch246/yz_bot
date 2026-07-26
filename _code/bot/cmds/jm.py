'''jm!'''
from jmcomic import *
import os
from main import cache, cq, read_params, send, to_thread, pages

client = JmOption.default().new_jm_client()

def run(body:str):
    '''禁漫获取
格式:
.jm search <params>
.jm <book_id:int>'''
    msg = cache.thismsg()
    body = cq.unescape(body)
    arg, last = read_params(body)

    if arg=='search' and last.strip():
        return search(last.strip())

    if arg.isdigit():
        download(int(arg), msg) # type: ignore
        return "解析中"
    return run.__doc__

def search(param):
    page: JmSearchPage = client.search_site(search_query=param, page=1)
    print(f'结果总数: {page.total}, 分页大小: {page.page_size}，页数: {page.page_count}')
    return pages.display([f'[{album_id}]: {title}' for album_id, title in page])


@to_thread
def download(book_id: int, msg):
    path = os.path.abspath(f'data/jm/{book_id}.pdf')
    if not os.path.isfile(path):
        option = create_option_by_file('data/option.yml')
        option.download_album(book_id)

    if not os.path.isfile(path):
        return send(f'下载失败，文件"{path}"不存在', **msg)

    send(cq.dump({
        'type':'file',
        'data':{
            'file':f'file://{path}'
        }
    }), **msg)

    return send(cq.dump({
        'type':'reply',
        'data':{
            'id': msg['message_id']
        }
    }) + '你的本子已下载并转换为PDF，已发送给你！', **msg)
