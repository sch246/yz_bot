from yz.tool.tool import dicts
import json
import os

config_file = 'Config.json'

init = {
}

def init_config():
    with open(config_file,'w',encoding='utf-8') as f:
        f.write(json.dumps(init, indent=4,ensure_ascii=False))

def load_config(*args):
    with open(config_file,encoding='utf-8') as f:
        return dicts.get(json.loads(f.read()),*args)
def save_config(value, *args):
    with open(config_file,encoding='utf-8') as f:
        J = json.loads(f.read())
    dicts.set(J, value, *args)
    with open(config_file,'w',encoding='utf-8') as f:
        f.write(json.dumps(J, indent=4,ensure_ascii=False))

def init_or_load_config(init_dic:dict):
    if not os.path.exists(config_file):
        init_config()
    with open(config_file,encoding='utf-8') as f:
        J = json.loads(f.read())
    dic = dicts.update(init_dic,J)
    with open(config_file,'w',encoding='utf-8') as f:
        f.write(json.dumps(dic, indent=4,ensure_ascii=False))
    return dic

def dict_save_config(dic:dict):
    if not os.path.exists(config_file):
        init_config()
    with open(config_file,encoding='utf-8') as f:
        J = json.loads(f.read())
    dic = dicts.update(J, dic)
    with open(config_file,'w',encoding='utf-8') as f:
        f.write(json.dumps(dic, indent=4,ensure_ascii=False))
    


def test():
    print('test')