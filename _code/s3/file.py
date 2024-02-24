from os.path import join
import os
import re
import json

path = '.'

def ensure_file(path:str):
    '''确保文件路径存在，并且返回文件路径(可能会与输入不一致)
    无法确保文件夹路径时报错'''
    # assert isinstance(path,str) and can_be_filename(path)
    os.makedirs(os.path.split(path)[0],exist_ok=True)

    if os.path.isdir(path):
        i = 0
        dirfile, ex = os.path.splitext(path)
        while os.path.isdir(dirfile + f'_{i}' + ex):
            i += 1
            if i>99:
                raise Exception('循环超过上限')
        path = dirfile + f'_{i}' + ex

    return path

def read(file_path, start_line=None, end_line=None):
    file_path = join(path, file_path)
    with open(file_path, 'r', encoding='utf-8') as f:
        return ''.join(f.readlines()[start_line:end_line])


def add(file_path, text):
    file_path = join(path, file_path)
    with open(file_path, 'a', encoding='utf-8') as f:
        f.write(text)


def write(file_path, text):
    file_path = join(path, file_path)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(text)


def overwrite(file_path, text: str, start_line=None, end_line=None):
    file_path = join(path, file_path)
    with open(file_path, 'r', encoding='utf-8') as f:
        lst = f.readlines()
    lst[start_line:end_line] = text.splitlines()
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lst))

def getpath(file_path):
    return join(path, file_path)

def json_read(file_path, **kws):
    file_path = join(path, file_path)
    with open(file_path, 'r',encoding='utf-8') as f:
        return json.load(f, **kws)

def json_write(file_path, obj, **kws):
    file_path = join(path, file_path)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=4, ensure_ascii=False, **kws)


def let_be_filename(title):
    '''符合文件命名'''
    rstr = r"[\/\\\:\*\?\"\<\>\|]"  # '/ \ : * ? " < > |'
    new_title = re.sub(rstr, "_", title).strip()  # 替换为下划线
    return new_title

def can_be_filename(name):
    '''这里没有检测空格'''
    return not (set(name) & set(r'/\:*?"<>|'))






















