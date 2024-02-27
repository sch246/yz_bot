'''处理cq码相关的东西'''
import re,os
import shutil
import requests

from main import str_tool, connect, to_thread

image_path = 'data/images'

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


re_CQdatas = r'(?:,[^,=]+=[^,\]]+)*'

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

def generate_unique_filename(directory):
    filenames = os.listdir(directory)
    i = 0
    while f'{i}.image' in filenames:
        i += 1
    return f'{i}.image'


def download_img(picture_url, name=None):
    if name is None:
        name = generate_unique_filename(image_path)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36             (KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36 QIHU 360SE",
        }
    r = requests.get(picture_url, headers=headers)
    with open(os.path.join(image_path,name), 'wb') as f:
        f.write(r.content)
    return os.path.join(image_path,name)

def url2cq(url:str,name:str=None):
    img = download_img(url,name).replace('\\','/')
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
                return url2cq(CQ['data'].get('url'),CQ['data'].get('file'))
            except:
                return cq
        return cq
    return re_CQ.sub(f,text)
