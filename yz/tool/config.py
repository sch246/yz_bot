from yz.tool.tool import dicts
import json
import os

init = {
}

def load_config(*args):
    with open('Config.json',encoding='utf-8') as f:
        return dicts.get(json.loads(f.read()),*args)

def default_or_load_config(default_dic:dict):
    if not os.path.exists('Config.json'):
        with open('Config.json','w',encoding='utf-8') as f:
            f.write(json.dumps(init, indent=4,ensure_ascii=False))
    with open('Config.json',encoding='utf-8') as f:
        J = json.loads(f.read())
    dic = dicts.update(default_dic,J)
    with open('Config.json','w',encoding='utf-8') as f:
        f.write(json.dumps(dic, indent=4,ensure_ascii=False))
    return dic


def save_config(value, *args):
    with open('Config.json',encoding='utf-8') as f:
        J = json.loads(f.read())
    dicts.set(J, value, *args)
    with open('Config.json','w',encoding='utf-8') as f:
        f.write(json.dumps(J, indent=4,ensure_ascii=False))

def test():
    print('test')