import shutil
import os
from uuid import uuid4

from .file import _send_file, _get, _set
from main import cache, cq, connect, send, read_params, is_msg, is_file

def _dir_send(path):
    msg = cache.thismsg()
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return f'路径"{path}"不存在'
    if not os.path.isdir(path):
        return f'路径"{path}"不是文件夹'
    try:
        tmp_zip = f'/tmp/{uuid4().hex}.zip'
        shutil.make_archive(tmp_zip.replace('.zip', ''), 'zip', path)
        ret = _send_file(tmp_zip)
        os.remove(tmp_zip)  # 发完即删除临时文件
        return ret
    except Exception as e:
        return f'压缩出错: {e}'

def _dir_get(path):
    return _dir_send(path)

def _dir_recv_zip(file_msg, dest_dir):
    CQ = cq.find_all(file_msg['message'])[0]
    file_id = cq.load(CQ)['data']['file_id']
    ret = connect.call_api('get_file', file_id=file_id)
    if ret['retcode'] != 0:
        return ret['wording']
    abs_path = ret['data']['file']
    tmp_zip_path = f'/tmp/{uuid4().hex}.zip'
    shutil.move(abs_path, tmp_zip_path)
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)  # 删除已有文件夹，执行强制覆盖
    os.makedirs(dest_dir, exist_ok=True)
    shutil.unpack_archive(tmp_zip_path, dest_dir)
    os.remove(tmp_zip_path)
    send(f'文件夹内容已成功解压到\n{dest_dir}', **file_msg)

# 是生成器
def _dir_set(path, is_force):
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

# 主run函数中新增分支：
def run(body:str):
    msg = cache.thismsg()
    if not msg['user_id'] in cache.ops:
        if not cache.any_same(msg, r'\.dir'):
            return '权限不足(一定消息内将不再提醒)'
        return

    body = cq.unescape(body)
    lines = body.splitlines()
    while len(lines)<2:
        lines.append('')
    first_line, *_ = lines

    s, last = read_params(first_line)
    if s=='get':
        path, last = read_params(last)
        if os.path.isfile(path):
            return _get(path)
        elif os.path.isdir(path):
            return _dir_get(path)
        return '目标不存在'
    elif s=='set':
        path, extra, last = read_params(last, 2)
        if extra not in ['-y', '-f']:
            last = extra  # 没有指定强制参数，则参数后移
            extra = ''
        if last.strip()=='dir':
            return _dir_set(path, extra in ['-y', '-f'])
        return _set(path, extra in ['-y', '-f'])
    return run.__doc__
