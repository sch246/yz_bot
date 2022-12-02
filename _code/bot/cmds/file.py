'''文件操作相关的命令'''

import traceback
import re
import os

from main import cq, connect, cache, send, to_thread, is_file, is_msg, is_img, read_params, getint, file


from s3.counter import Counter
def insert_linemark(s:str):
    c = Counter()
    return '\n'.join(map(lambda s:str(next(c))+'│'+s, s.splitlines()))

def _strip_linemark(line:str):
    return re.sub(r'^\d+\|','',line, 1)

def strip_linemark(s:str):
    return '\n'.join(map(_strip_linemark, s.splitlines()))

def run(body:str):
    '''查看和编辑文件，私聊似乎没办法传文件(api错误)，set可以读取之后发送的文件/图片，to可以读取10条消息内最近的文件
.file
 : read <文件路径> [-i:是否显示行号] [<起始行:int> <结束行:int>]
 | write <文件路径> [<起始行:int> <结束行:int>]\\n<内容>
 | get <文件路径>
 | set <文件路径> [-y/-f:是否覆盖已有文件] || <文件>
<文件> || .file to <文件路径> [-y/-f:是否覆盖已有文件]
'''
    msg = cache.get_last()
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, r'\.file'):
            return '权限不足(一定消息内将不再提醒)'
        return

    body = cq.unescape(body)
    lines = body.splitlines()
    while len(lines)<2:
        lines.append('')
    first_line, *last_lines = lines

    s, last = read_params(first_line)
    if s=='read':
        path, *params, last = read_params(last, 4)
        if not params[0]=='-i':
            i, start, end = False, *params[:2]
        else:
            i, start, end = True, *params[1:]
        return _read(path or '.', i, getint(start), getint(end))
    elif s=='write':
        path, start, end, last = read_params(last, 3)
        if not os.path.isfile(path):
            return '目标不是文件'
        return _write(path, getint(start), getint(end), last_lines)
    elif s=='get':
        path, last = read_params(last)
        if not os.path.isfile(path):
            return '目标不是文件'
        return _get(path)
    elif s=='set':
        path, extra, last = read_params(last, 2)
        return _set(path, extra in ['-y', '-f'])
    elif s=='to':
        path, extra, last = read_params(last, 2)
        return _to(path, extra in ['-y', '-f'])
    return run.__doc__

def read_text(text, start=None, end=None):
    return '\n'.join(insert_linemark(text).splitlines()[start:end])

def read_file(path, with_linemark, start=None, end=None):
    with open(path,encoding='utf-8') as f:
        text = f.read()
        if with_linemark:
            return read_text(text, start, end)
        else:
            return text

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

def _read(path, with_linemark, start, end):
    if os.path.isdir(path):
        return listdir(path)
    try:
        text = read_file(path, with_linemark, start, end)
        if text=='':
            return '文件为空'
        else:
            return text
    except Exception as e:
        return e


def _write(path, start, end, lines):
    try:
        file.overwrite(path,'\n'.join(lines),start,end)
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



def _get(path):
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

def recv_img(_msg, path):
    if not is_img(_msg):
        return '目标msg不是单个图片'
    download(cq.load(_msg['message'])['data']['url'], path, cache.get_last())
    return f'正在将图片保存到\n{path}'

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
def _set(path, is_force):
    if is_force or not os.path.exists(path):
        reply = yield '请发送一个文件/图片'
        if is_file(reply):
            return _recv_file(reply, path)
        elif is_img(reply):
            return recv_img(reply, path)
    elif os.path.isfile(path):
        reply = yield '文件已存在，确定要覆盖文件吗(y/n)'
        if is_file(reply):
            return _recv_file(reply, path)
        elif is_img(reply):
            return recv_img(reply, path)
        if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
            return '操作终止'
        file_msg = yield '请发送一个文件'
        return _recv_file(file_msg, path)
    return '找到了文件，但是发送失败了'


def _to(path, is_force):
    file_msg = cache.get_one(cache.get_last(), is_file, 10)# 接收10条消息以内任何人发的文件
    if not file_msg:
        return '10条消息内没有文件'
    if is_force or not os.path.exists(path):
        return _recv_file(file_msg, path)
    elif os.path.isfile(path):
        reply = yield '文件已存在，确定要覆盖文件吗(y/n)'
        if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
            return '操作终止'
        return _recv_file(file_msg, path)
    return '找到了文件，但是发送失败了'
