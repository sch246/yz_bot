'''提供一个存储空间，在程序启动时自动加载，关闭时自动保存，以多个json文件的形式存储(不能直接查看的数据是不好的！)'''
import os
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
    global storage
    for root, dirs, files in os.walk(root_path):
        name_space = root[len(root_path):]
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
    global storage
    for name_space in storage.keys():
        root = join(root_path,name_space)
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
        for name in storage[name_space].keys():
            path = join(root,name+'.json')
            # 这里没有做检验
            json_write(path, storage[name_space][name])


def get_namespace(name_space):
    storage.setdefault(name_space,{})
    return storage[name_space]

def get(name_space, name):
    storage.setdefault(name_space,{})
    storage[name_space].setdefault(name,{})
    return storage[name_space][name]

load()
import atexit
atexit.register(save)