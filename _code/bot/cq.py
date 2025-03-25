'''处理cq码相关的东西'''
import re,os,io
import shutil
import requests
import threading
import time
from PIL import Image

from main import str_tool, connect, to_thread

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
        target_dir = temp_path
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
    # 如果未提供name，但是是临时的，使用url的哈希值作为缓存文件名
    elif temp:
        url_hash = hashlib.md5(picture_url.encode('utf-8')).hexdigest()
        # 在缓存中寻找可能存在的文件，避免重复下载
        existing_files = [f for f in os.listdir(target_dir) if os.path.splitext(os.path.basename(f))[0]==url_hash]
        if existing_files:
            # 有缓存直接返回
            return os.path.join(target_dir, existing_files[0])

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
        if temp: # 临时的 hash
            name = url_hash
        else: # 否则 按照序号排序
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
