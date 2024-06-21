# 兼容之前的版本，用于将images里的图片重命名，同时同步caves
# 请在关闭bot的情况下运行此脚本，否则bot关闭后会把修改后的cave又覆盖掉
# 它生成了额外的cave.json.bak，如果遇到了上述情况请手动合并

import json
import re
import atexit


def replace_by_dic(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(k, v)
    return s
def replace_by_dic2(s: str, d: dict):
    for k, v in d.items():
        s = s.replace(v, k)
    return s

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
    return replace_by_dic(text, escape_dic)

def unescape(text: str):
    '''将CQ码的一团转义成正常文本'''
    return replace_by_dic2(text, escape_dic)

def escape2(text: str):
    '''将正常文本转义成CQ码的一团'''
    return replace_by_dic(text, escape_dic2)

def unescape2(text: str):
    '''将CQ码的一团转义成正常文本'''
    return replace_by_dic2(text, escape_dic2)

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




cave = open('data/storage/cave.json',encoding='utf-8').read()
print(type(cave))

cave_names = []

for CQ in find_all(cave):
    cq = load(CQ)
    name = cq['data'].get('file')
    if name:
        cave_names.append(name[30:])


import os
import time
from PIL import Image

directory = 'data/images/'

date_dic = {}

for filename in os.listdir(directory):
    filepath = os.path.join(directory, filename)

    # 删除不在回声洞的文件
    if filename not in cave_names:
        # input(f'是否删除{filename}')
        try:
            os.remove(filepath)
        except:
            pass
        continue

    # 跳过文件夹
    if os.path.isdir(filepath):
        continue

    # 构造名字
    mod_time_stamp = os.path.getmtime(filepath)
    creation_date = time.strftime('%Y-%m-%d', time.localtime(mod_time_stamp))
    if creation_date not in date_dic:
        i = 0
        fs = os.listdir(directory)
        while list(filter(lambda x:x.startswith(f'{creation_date}-{i}'), fs)):
            i += 1
        date_dic[creation_date] = i
    else:
        date_dic[creation_date] += 1
    i = date_dic[creation_date]
    name = f'{creation_date}-{i}'

    # 判断类型
    try:
        with Image.open(filepath) as img:
            img_format = img.format.lower()

            if img_format == 'jpeg':
                new_name = name + '.jpg'
            elif img_format == 'png':
                new_name = name + '.png'
            elif img_format == 'gif':
                new_name = name + '.gif'
            else:
                # 看起来不是需要处理的图片文件
                continue
    except (IOError, ValueError) as e:
        print(f'Could not process file {filepath}: {e}')
        continue

    # 写入文件
    new_filepath = os.path.join(directory, new_name)
    os.rename(filepath, new_filepath)
    cave = cave.replace(filepath, new_filepath)
    print(f'Renamed {filepath} to {new_filepath}')

@atexit.register
def save():
    with open('data/storage/cave.json','w',encoding='utf-8') as f:
        f.write(cave)
    with open('data/storage/cave.json.bak','w',encoding='utf-8') as f:
        f.write(cave)
