'''文件操作相关的命令'''

import shutil
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
    有一些bug
.file
 : read <文件路径> [-i:是否显示行号] [<起始行:int> <结束行:int>]
 | write <文件路径> [<起始行:int> <结束行:int>]\\n<内容>
 | get <文件路径/目录路径>
 | del <文件路径> [-r:删除目录]
 | set <文件路径> [-y/-f:是否覆盖已有文件] || <文件>
 | <文件> || .file to <文件路径> [-y/-f:是否覆盖已有文件]
 | setdir <目录路径> [-y/-f:是否覆盖已有目录] || <压缩包>
 | <压缩包> || .file dirto <目录路径> [-y/-f:是否覆盖已有目录]
'''
    msg = cache.thismsg()
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
        if os.path.isfile(path):
            return _get(path)
        elif os.path.isdir(path):
            return _getdir(path)
        return '目标不存在'
    elif s=='set':
        path, extra, last = read_params(last, 2)
        return _set(path, extra in ['-y', '-f'])
    elif s=='del':
        path, extra, last = read_params(last, 2)
        return _del(path, extra in ['-r'])
    elif s=='to':
        path, extra, last = read_params(last, 2)
        return _to(path, extra in ['-y', '-f'])
    elif s=='setdir':
        path, extra, last = read_params(last, 2)
        return _setdir(path, extra in ['-y', '-f'])
    elif s=='dirto':
        path, extra, last = read_params(last, 2)
        return _dirto(path, extra in ['-y', '-f'])
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
    msg = cache.thismsg()
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return f'打开失败，文件"{path}"不存在'
    return cq.dump({
        'type':'file',
        'data':{
            'file':f'file://{path}'
        }
    })
    # if 'group_id' in msg.keys():
    #     ret = connect.call_api('upload_group_file', group_id=msg['group_id'], file=path, name=os.path.split(path)[1])
    #     if not ret['retcode']==0:
    #         return ret['wording']
    #     return
    # elif 'user_id' in msg.keys():
    #     ret = connect.call_api('upload_private_file', user_id=msg['user_id'], file=path, name=os.path.split(path)[1])
    #     if not ret['retcode']==0:
    #         return ret['wording']
    #     return
    # return '找到了文件，但是发送失败了'



def _get(path):
    return _send_file(path)


def _dir_send(path):
    msg = cache.thismsg()
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return f'路径"{path}"不存在'
    if not os.path.isdir(path):
        return f'路径"{path}"不是文件夹'
    try:
        tmp_zip = f'/tmp/tmp.zip'
        shutil.make_archive(tmp_zip.replace('.zip', ''), 'zip', path)
        ret = _send_file(tmp_zip)
        return ret
    except Exception as e:
        return f'压缩出错: {e}'

def _getdir(path):
    return _dir_send(path)

def _rm(path, isdir=False):
    try:
        if isdir:
            shutil.rmtree(path,True)
        else:
            os.remove(path)
        return f'已删除: {path}'
    except Exception as e:
        return f'删除出错: {e}'

def _del(path: str, isdir=False):
    msg = cache.thismsg()
    if any(f(path) for f in [lambda x:re.match('^/([^/]+(/)?)?$', x)]):
        return '危险操作，已禁用'
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return f'路径"{path}"不存在'
    elif os.path.isfile(path):
        return _rm(path)
    elif os.path.isdir(path):
        if isdir:
            return _rm(path, True)
        else:
            reply = yield '目标是目录而不是文件，确定要删除吗(y/n)'
            if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
                return '操作终止'
            return _rm(path, True)
    else:
        return '未知错误，遇到了不是文件也不是文件夹的东西'


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
    download(cq.load(_msg['message'])['data']['url'], path, cache.thismsg())
    return f'正在将图片保存到\n{path}'

def _recv_file(file_msg, path):
    CQ = cq.find_all(file_msg['message'])[0]
    file_id = cq.load(CQ)['data']['file_id']
    ret = connect.call_api('get_file', file_id=file_id)
    if not ret['retcode']==0:
        return ret['wording']
    abs_path = ret['data']['file']
    os.rename(abs_path, path)
    send(f'文件已保存到\n{path}', **file_msg)

    # if 'file' not in file_msg.keys():
    #     return '发送的不是文件，接收终止'
    # # 离线文件具有url，群文件需要调用api获取链接
    # if 'group_id' in file_msg.keys() and file_msg['group_id']!=None:
    #     file = file_msg['file']
    #     ret = connect.call_api('get_group_file_url',group_id=file_msg['group_id'], file_id=file['id'], busid=file['busid'])
    #     if ret['retcode']==0:
    #         url = ret['data']['url']
    # else:
    #     url = file_msg['file']['url']
    # download(url, path, file_msg)
    # return f'正在将文件保存到\n{path}'

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
    file_msg = cache.get_one(cache.thismsg(), is_file, 10)# 接收10条消息以内任何人发的文件
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

def _dir_recv_zip(file_msg, dest_dir):
    CQ = cq.find_all(file_msg['message'])[0]
    file_id = cq.load(CQ)['data']['file_id']
    ret = connect.call_api('get_file', file_id=file_id)
    if ret['retcode'] != 0:
        return ret['wording']
    abs_path = ret['data']['file']
    tmp_zip_path = f'/tmp/tmp.zip'
    shutil.move(abs_path, tmp_zip_path)
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)  # 删除已有文件夹，执行强制覆盖
    os.makedirs(dest_dir, exist_ok=True)
    shutil.unpack_archive(tmp_zip_path, dest_dir)
    os.remove(tmp_zip_path)
    send(f'文件夹内容已成功解压到\n{dest_dir}', **file_msg)

# 是生成器
def _setdir(path, is_force):
    if is_force or not os.path.exists(path):
        reply = yield '请发送一个zip压缩文件（包含要解压的文件夹内容）'
        if is_file(reply):
            return _dir_recv_zip(reply, path)
        return '发送的不是文件，操作终止'
    elif os.path.isdir(path):
        reply = yield '目标文件夹已存在，确定要覆盖吗(y/n)'
        if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
            return '操作终止'
        file_msg = yield '请发送一个zip压缩文件（包含要解压的文件夹内容）'
        return _dir_recv_zip(file_msg, path)
    return '该路径已存在但不是文件夹，操作终止'


def _dirto(path, is_force):
    file_msg = cache.get_one(cache.thismsg(), is_file, 10)# 接收10条消息以内任何人发的文件
    if not file_msg:
        return '10条消息内没有文件'
    if is_force or not os.path.exists(path):
        return _dir_recv_zip(file_msg, path)
    elif os.path.isdir(path):
        reply = yield '目标目录已存在，确定要覆盖吗(y/n)'
        if not is_msg(reply) or not reply['message'] in ['是','确定','y','Y','yes','Yes','YES','OK','ok','Ok']:
            return '操作终止'
        return _dir_recv_zip(file_msg, path)
    return '该路径已存在但不是文件夹，操作终止'
