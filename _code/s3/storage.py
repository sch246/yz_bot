'''提供一个存储空间，在程序启动时自动加载，关闭时自动保存，以多个json文件的形式存储(不能直接查看的数据是不好的！)'''
import os
from typing import Callable
from main import file

json_read, json_write = file.json_read, file.json_write
join = os.path.join

root_path = 'data/storage/'

os.makedirs(root_path,exist_ok=True)

storage = {}
# 它的第一级为命名空间，作为文件路径
# 第二级是作为文件名
# 第三级开始才允许字符串以外的键

def load():
    for root, _, files in os.walk(root_path):
        name_space = root[len(root_path):].replace('\\','/')
        storage[name_space] = {}
        for file in files:
            name = file[:-5]
            path = join(root,file)
            if file.endswith('.json'):
                try:
                    storage[name_space][name] = json_read(path)
                except:
                    # 懒
                    print(f'读取"{path}"时发生错误')

def save():
    '''退出时保存storage'''
    for s_name, s_value in storage.items():
        root = join(root_path,s_name)
        try:
            os.makedirs(root,exist_ok=True)
        except FileExistsError:
            print(f'{root}: 对应文件夹的位置已经有同名文件')
            continue
        except OSError:
            print(f'{root}: 命名空间是作为文件路径的，文件路径不合法')
            continue
        except Exception as e:
            print(f'{root}: {e}')
            continue
        for name, value in s_value.items():
            path = join(root,name+'.json')
            # 非基本键值将被跳过
            # 非基本对象将会被设为null
            json_write(path, value, skipkeys=True, default=lambda x:None)


def get_namespace(name_space):
    storage.setdefault(name_space,{})
    return storage[name_space]

def get(name_space:str, name:str, default:Callable=dict):
    storage.setdefault(name_space,{})
    storage[name_space].setdefault(name,default())
    return storage[name_space][name]

load()
import atexit
atexit.register(save)