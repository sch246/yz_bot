

import traceback
import re
import os

from main import cq, connect, cache, send, to_thread, is_file, is_msg

def getint(s):
    try:
        return int(s)
    except:
        return None

from s3.counter import Counter
def insert_linemark(s:str):
    c = Counter()
    return ''.join(map(lambda s:str(next(c))+'│'+s, s.splitlines(True)))

def _strip_linemark(line:str):
    return re.sub(r'^\d+\|','',line, 1)

def strip_linemark(s:str):
    return ''.join(map(_strip_linemark, s.splitlines(True)))

def run(body:str):
    '''查看和编辑文件，私聊似乎没办法传文件(api错误)，set可以读取之后发送的文件，to可以读取10条消息内最近的文件
.file
 : read <文件路径> [<起始行> <结束行>]
 | write <文件路径> [<起始行> <结束行>]\\n<内容>
 | get <文件路径>
 | set <文件路径> || <文件>
<文件> || .file to <文件路径>
'''
    msg = cache.get_last()
    if not msg['user_id'] in cache.get_ops():
        if not cache.any_same(msg, '\.file'):
            return '权限不足(一定消息内将不再提醒)'
        return

    body = cq.unescape(body)
    lines = body.splitlines()
    if not lines:
        lines = ['']
    head = lines[0].strip()
    value = lines[1:]

    m = re.match(r'read ([\S]+)(.*)', head)
    if m:
        return _read(m)
    m = re.match(r'write ([\S]+)(.*)', head)
    if m:
        return _write(m, value)
    m = re.match(r'get ([\S]+).*', head)
    if m:
        return _get(m)
    m = re.match(r'set ([\S]+)(.*)', head)
    if m:
        return _set(m)
    m = re.match(r'to ([\S]+)(.*)', head)
    if m:
        return _to(m)

    return run.__doc__

def read_text(text, start=None, end=None):
    return ''.join(insert_linemark(text).splitlines(True)[start:end])

def read_file(path, start=None, end=None):
    with open(path,encoding='utf-8') as f:
        text = f.read()
        return read_text(text, start, end)

def listitems(path):
    lst = os.listdir(path)
    dirs = []
    files = []
    for item in lst:
        if os.path.isdir(os.path.join(path,item)):
            dirs.append(item)
        else:
            files.append(item)
    dirs.sort()
    files.sort()
    return dirs, files
def listdir(path='.'):
    dirs, files = listitems(path)
    return '\n'.join((*map(lambda s:'> '+s, dirs),*files))

def _read(m):
    path = m.group(1)
    if os.path.isdir(path):
        return listdir(path)
    extra_param = m.group(2).strip()
    if extra_param:
        params = list(map(getint, extra_param.split(' ')))
        if len(params)==1:
            params.append(None)
    else:
        params = [None,None]
    try:
        text = read_file(path, params[0], params[1])
        if text=='':
            return '文件为空'
        else:
            return text
    except Exception as e:
        return e

def write_text(text, lines, start=None, end=None):
    textlines = text.splitlines(True)
    textlines[start:end] = map(_strip_linemark, lines)
    return ''.join(textlines)

def write_file(path, lines, start=None, end=None):
    with open(path,'w',encoding='utf-8') as f:
        f.write(write_text(f.read(), lines, start, end))


def _write(m, lines):
    path = m.group(1)
    extra_param = m.group(2).strip()
    if extra_param:
        params = list(map(getint, extra_param.split(' ')))
        if len(params)==1:
            params.append(None)
    try:
        write_file(path,lines,params[0],params[1])
        return '已写入 '+path
    except Exception as e:
        return e

def _send_file(path):
    msg = cache.get_last()
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return f'打开失败，文件"{path}"不存在'
    if 'group_id' in msg.keys():
        ret = connect.call_api('upload_group_file', group_id=msg['group_id'], file=path, name=os.path.split(path)[1])
        if not ret['retcode']==0:
            return ret['wording']
        return
    elif 'user_id' in msg.keys():
        ret = connect.call_api('upload_private_file', user_id=msg['user_id'], file=path, name=os.path.split(path)[1])
        if not ret['retcode']==0:
            return ret['wording']
        return
    return '找到了文件，但是发送失败了'



def _get(m):
    path = m.group(1)
    return _send_file(path)



@to_thread
def download(url, path, msg):
    try:
        import requests
        r = requests.get(url, stream=True)
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=512):
                f.write(chunk)
    except:
        send(''.join(traceback.format_exc().splitlines(True)[:]), **msg)
    send(f'文件已保存到\n{path}', **msg)

def _recv_file(file_msg, path):
    if 'file' not in file_msg.keys():
        return '发送的不是文件，接收终止'
    # 离线文件具有url，群文件需要调用api获取链接
    if 'group_id' in file_msg.keys() and file_msg['group_id']!=None:
        file = file_msg['file']
        ret = connect.call_api('get_group_file_url',group_id=file_msg['group_id'], file_id=file['id'], busid=file['busid'])
        if ret['retcode']==0:
            url = ret['data']['url']
    else:
        url = file_msg['file']['url']
    download(url, path, file_msg)
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
        if is_file(reply):
            return _recv_file(reply, path)
        if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
            return '操作终止'
        file_msg = yield f'请发送一个文件'
        return _recv_file(file_msg, path)
    return '找到了文件，但是发送失败了'


def _to(m):
    file_msg = cache.get_one(is_file, 10)# 接收10条消息以内任何人发的文件
    if not file_msg:
        return '10条消息内没有文件'
    path = m.group(1)
    extra_param = m.group(2).strip()
    params=[]
    if extra_param:
        params = extra_param.split(' ')
    if '-y' in params or not os.path.exists(path):
        return _recv_file(file_msg, path)
    elif os.path.isfile(path):
        reply = yield '文件已存在，确定要覆盖文件吗(y/n)'
        if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
            return '操作终止'
        return _recv_file(file_msg, path)
    return '找到了文件，但是发送失败了'