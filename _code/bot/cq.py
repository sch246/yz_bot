'''处理cq码相关的东西'''
import re,os,io
import base64
import binascii
import shutil
import requests
import threading
import time
from PIL import Image

from main import str_tool, connect, to_thread
from s3.url_to_base64 import (
    maybe_prune_image_cache,
    resolve_image_uri,
    touch_image_cache,
)

image_path = 'data/images'
temp_path = 'data/tmp_files'

if not os.path.exists(image_path):
    os.mkdir(image_path)
if not os.path.exists(temp_path):
    os.mkdir(temp_path)

escape_dic={ # CQ码内的转义
    '&':'&amp;',
    '[':'&#91;',
    ']':'&#93;',
    ',':'&#44;'
}
escape_dic2={ # CQ码外的转义
    '&':'&amp;',
    '[':'&#91;',
    ']':'&#93;'
}

def escape(text: str):
    '''将正常文本转义成CQ码的一团'''
    return str_tool.replace_by_dic(text, escape_dic)

def unescape(text: str):
    '''将CQ码的一团转义成正常文本'''
    return str_tool.replace_by_dic2(text, escape_dic)

def escape2(text: str):
    '''将正常文本转义成CQ码的一团'''
    return str_tool.replace_by_dic(text, escape_dic2)

def unescape2(text: str):
    '''将CQ码的一团转义成正常文本'''
    return str_tool.replace_by_dic2(text, escape_dic2)


re_CQdatas = r'(?:,[^,=]+=[^,\]]*)*'

_re_CQ = re.compile(rf'\[CQ:[^,\]]+{re_CQdatas}\]')
re_CQ = re.compile(rf'\[CQ:(?P<type>[^,\]]+)(?P<data>{re_CQdatas})\]')

def find_all(s:str):
    return _re_CQ.findall(s)

def load(CQ:str):
    '''将字符串形式的单个CQ转化为字典，并且将其中乱七八糟的东东转化为正常'''
    CQ = re.sub(r'\s','',CQ)  # 去掉空白符
    mt = re_CQ.match(CQ)
    stype=mt.group('type')
    sdata=mt.group('data')
    if sdata:
        # 若CQ有参数,分割并获取参数字符串,再次分割并转化为字典
        str_list = sdata[1:].split(',')
        def f(s:str):
            s = unescape(s)
            i = s.index('=')
            return s[:i], s[i+1:]
        data = dict(map(f ,str_list))
    else:
        data={}
    return {'type':stype,'data':data}

def dump(d:dict):
    '''将字典形式的CQ转化为字符串形式，并且将对应的字符转换为CQ的乱七八糟的东东'''
    type=d['type']
    data = ''.join(map(lambda x:','+escape(f'{x[0]}={x[1]}'), d['data'].items()))
    return f'[CQ:{type}{data}]'


def cq(type, **data):
    return dump({
        'type':type,
        'data':data
    })

def download_img(url:str, name:str=None):
    reply = connect.call_api('download_file',url=url)
    if reply['retcode']!=0:
        raise Exception('warning: 图片下载失败')
    file_path = reply['data']['file']
    print('download:', file_path)

    if name is None:
        name = os.path.basename(file_path)
    target_path = os.path.join(image_path, name)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    shutil.move(file_path, target_path)
    return os.path.abspath(target_path)

lock = threading.Lock()

def generate_unique_filename(directory):
    with lock:
        filenames:list[str] = os.listdir(directory)
        date = time.strftime('%Y-%m-%d')
        i = 0
        while list(filter(lambda x:x.startswith(f'{date}-{i}'), filenames)):
            i += 1
        return f'{date}-{i}.cache'

import hashlib
def download_img(picture_url, name=None, temp=True):
    if temp:
        # Temporary network images share the content-addressed resolver used by
        # vision and reference-image tools. ``name`` is intentionally ignored:
        # a cache filename identifies bytes, not a caller-provided label.
        file_path, _ = resolve_image_uri(picture_url, temp_path)
        return file_path
    else:
        target_dir = image_path

    # 检查并创建目标目录
    os.makedirs(target_dir, exist_ok=True)
    # 如果提供了文件名，则检查文件是否已存在
    if name:
        potential_files = [name, name.rsplit('.', 1)[0] + '.jpg', 
                           name.rsplit('.', 1)[0] + '.png', 
                           name.rsplit('.', 1)[0] + '.gif']
        for fname in potential_files:
            file_path = os.path.join(target_dir, fname)
            if os.path.exists(file_path):
                return file_path

    # 下载
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36             (KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36 QIHU 360SE",
        }
    r = requests.get(picture_url, headers=headers)
    if r.status_code != 200:
        raise Exception(f"Failed to download image, status code: {r.status_code}")

    # 获取图片格式并确定扩展名
    with Image.open(io.BytesIO(r.content)) as img:
        img_format = img.format.lower()
    if img_format == 'jpeg':
        ext = '.jpg'
    elif img_format == 'png':
        ext = '.png'
    elif img_format == 'gif':
        ext = '.gif'
    else:
        ext = ''
    # 生成图片名(如果没有)
    if name is None:
        name = generate_unique_filename(target_dir)
    # 修改拓展名
    name = name.rsplit('.', 1)[0] + ext

    file_path = os.path.join(target_dir, name)

    with open(file_path, 'wb') as f:
        f.write(r.content)

    return file_path

def url2cq(url:str,name:str=None, temp=True):
    img = download_img(url,name, temp).replace('\\','/')
    return dump({
        'type':'image',
        'data':{
            'file':f'file://__botdir__/{img}'  # 设置一个魔术字符串
        }
    })

def base64_to_cq(image_base64: str):
    """把 API 返回的 Base64 图片缓存为本地临时文件并转为 CQ 码。"""
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, TypeError, ValueError) as error:
        raise ValueError('无效的 Base64 图片数据') from error

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_format = (image.format or '').lower()
            image.verify()
    except Exception as error:
        raise ValueError('Base64 数据不是可识别的图片') from error

    extensions = {
        'jpeg': '.jpg',
        'png': '.png',
        'webp': '.webp',
        'gif': '.gif',
    }
    extension = extensions.get(image_format)
    if extension is None:
        raise ValueError(f'不支持的图片格式: {image_format or "unknown"}')

    maybe_prune_image_cache(temp_path)
    filename = hashlib.sha256(image_bytes).hexdigest() + extension
    file_path = os.path.join(temp_path, filename)
    with lock:
        if os.path.exists(file_path):
            touch_image_cache(file_path)
        else:
            with open(file_path, 'wb') as file:
                file.write(image_bytes)

    cq_path = file_path.replace('\\', '/')
    return dump({
        'type': 'image',
        'data': {
            'file': f'file://__botdir__/{cq_path}'
        }
    })

def save_pic(text):
    def f(m:re.Match):
        cq = m.group(0)
        CQ = load(cq)
        if CQ['type']=='image':
            try:
                return url2cq(CQ['data'].get('url'), temp=False)
            except:
                return cq
        return cq
    return re_CQ.sub(f,text)
