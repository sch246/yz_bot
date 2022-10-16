import json
import os

config_file = 'config.json'

init = {
}


class dicts:
    @staticmethod
    def get(d,*k_):
        '''递归打开字典'''
        if  len(k_)==1:
            return d[k_[0]]
        else:
            return dicts.get(d[k_[0]],*k_[1:])

    @staticmethod
    def set(d, value, *k_):
        '''递归写入字典'''
        if  len(k_)==1:
            d[k_[0]] = value
        else:
            if k_[0] not in d.keys():
                d[k_[0]]={}
            dicts.set(d[k_[0]], value, *k_[1:])

    @staticmethod
    def update(d1, d2):
        '''递归更新字典
        参考:https://www.coder.work/article/1283998
        '''
        return {
            **d1, **d2,
            **{k: dicts.update(d1[k], d2[k]) for k in {*d1} & {*d2} if isinstance(d1[k],dict)}
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
def del_config(*args):
    with open(config_file,encoding='utf-8') as f:
        dic = dicts.get(json.loads(f.read()),*(list(args)[:-1]))
    del dic[args[-1]]
    save_config(dic,*(list(args)[:-1]))

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