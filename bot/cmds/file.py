
from bot.cq import unescape
from bot.connect import *
from bot.cmds import msg
from bot.cache import get_ops, get_last
from bot import I
from bot import send
import re
import os

def getint(s):
    try:
        return int(s)
    except:
        return None

from s3.counter import Counter
def insert_linemark(s:str):
    c = Counter()
    return ''.join(map(lambda s:str(next(c))+'│ '+s, s.splitlines(True)))

def run(body:str):
    '''查看和编辑文件
.file (read <文件路径> [<起始行> <结束行>]) | (get <文件路径>) | (set <文件路径> || <文件>) | (<文件> :: to <文件路径>)
|| 意味着需要分为多次消息发送
:: 意味着该命令会读取之前的消息'''

    global msg
    msg = get_last()
    if not msg['user_id'] in get_ops():
        return

    body = unescape(body)
    lines = body.splitlines()
    if not lines:
        lines = ['']
    head = lines[0].strip()
    value = lines[1:]

    m = re.match(r'read ([\S]+)(.*)', head)
    if m:
        return _read(m)
    m = re.match(r'get ([\S]+).*', head)
    if m:
        return _get(m)
    m = re.match(r'set ([\S]+)(.*)', head)
    if m:
        return _set(m)

    return run.__doc__

def _read(m):
    path = m.group(1)
    extra_param = m.group(2).strip()
    try:
        read_text = open(path,encoding='utf-8').read()
    except Exception as e:
        return f'打开失败\n{e}'
    read_text = insert_linemark(read_text)
    if extra_param:
        params = extra_param.split(' ')
        try:
            params = list(map(getint, params))
            return ''.join(read_text.splitlines(True)[params[0]:params[1]])
        except:
            pass
    else:
        return read_text

def _get(m):
    path = m.group(1)
    path=os.path.abspath(path)
    if not os.path.isfile(path):
        return f'打开失败，文件"{path}"不存在'
    if 'group_id' in msg.keys():
        ret = call_api('upload_group_file', group_id=msg['group_id'], file=path, name=os.path.split(path)[1])
        if not ret['retcode']==0:
            return ret['wording']
        return
    elif 'user_id' in msg.keys():
        ret = call_api('upload_private_file', user_id=msg['user_id'], file=path, name=os.path.split(path)[1])
        if not ret['retcode']==0:
            return ret['wording']
        return
    return '找到了文件，但是发送失败了'

from bot.connect import call_api
from s3.thread import to_thread
import traceback


from bot.msgs import is_msg

@to_thread
def download(url, path, imsg):
    try:
        import requests
        r = requests.get(url, stream=True)
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=512):
                f.write(chunk)
    except:
        send(''.join(traceback.format_exc().splitlines(True)[:]), **imsg)
    send(f'文件已保存到\n{path}', **imsg)

def _recv_file(file_msg, path):
    if 'file' not in file_msg.keys():
        return '发送的不是文件，接收终止'
    # 离线文件具有url，群文件需要调用api获取链接
    if 'group_id' in file_msg.keys() and file_msg['group_id']!=None:
        file = file_msg['file']
        ret = call_api('get_group_file_url',group_id=file_msg['group_id'], file_id=file['id'], busid=file['busid'])
        if ret['retcode']==0:
            url = ret['data']['url']
    else:
        url = file_msg['file']['url']
    download(url, path, I(file_msg))
    return f'正在将文件保存到\n{path}'

# 是生成器
def _set(m):
    path = m.group(1)
    extra_param = m.group(2).strip()
    params=[]
    if extra_param:
        params = extra_param.split(' ')
    if '-y' in params or not os.path.exists(path):
        file_msg = yield f'请发送一个文件'
        return _recv_file(file_msg, path)
    elif os.path.isfile(path):
        reply = yield '文件已存在，确定要覆盖文件吗(y/n)'
        if 'file' in reply.keys():
            return _recv_file(reply, path)
        if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
            return '操作终止'
        file_msg = yield f'请发送一个文件'
        return _recv_file(file_msg, path)
    return '找到了文件，但是发送失败了'