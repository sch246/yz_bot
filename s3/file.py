from os.path import join
import os

path = '.'

def ensure_file(path):
    '''确保文件路径存在，并且返回文件路径'''
    dirpath = os.path.split(path)[0]
    os.makedirs(dirpath,exist_ok=True)
    return path

def read(file_path, start_line=None, end_line=None):
    file_path = join(path, file_path)
    with open(file_path, 'r', encoding='utf-8') as f:
        return '\n'.join(f.readlines()[start_line:end_line])


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