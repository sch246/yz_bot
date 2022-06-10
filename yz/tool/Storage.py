import os
import pickle
import dill as pickle
from atexit import register as on_exit

from yz.tool.tool import mkdirs,fmt

class Storage:
    def __init__(self) -> None:
        self.base_path = './storage/'
        self.msg_locals_path = os.path.join(self.base_path, 'msg_locals.pkl')
        self.initfunc_path = os.path.join(self.base_path, 'initfunc.pkl')
        self.fmt = fmt('rg')+'storage> '
        
        self.msg_locals = {}
        self.msg = {}
        
        self.links = {}
        
        self.initfunc={}
    
    def prn(self,text):
        print(self.fmt+text+fmt())
    
    @on_exit
    def save_storage(self):
        self.prn('保存中')
        self.prn('保存msg_locals')
        self.save(self.msg_locals_path, self.msg_locals)
        self.prn('保存initfuncs')
        self.save(self.initfunc_path, self.initfunc)

    def load_storage(self,bot):
        self.prn(fmt('gb')+'加载中')
        if os.path.exists(self.msg_locals_path):
            self.prn(fmt('gb')+'载入msg_locals')
            obj = self.load(self.msg_locals_path)
            if isinstance(obj, dict):
                self.prn(fmt('gb')+f"载入 {obj}")
                self.msg_locals.update(obj)
            else:
                self.prn(fmt('r'),'异常，未载入内容')
        if os.path.exists(self.initfunc_path):
            self.prn(fmt('gb')+'载入initfuncs')
            obj = self.load(self.initfunc_path)
            if isinstance(obj, dict):
                self.prn(fmt('gb')+f"载入 {obj}")
                bot.initfuncs.extend(obj.values())
            else:
                self.prn(fmt('r'),'异常，未载入内容')

    def save(self, file_path, obj):
        mkdirs(self.base_path)
        if isinstance(obj,dict):
            for key in list(obj.keys()):
                try:
                    pickle.dumps(obj[key])
                except Exception as e:
                    print('>del ',obj[key])
                    print(e)
                    del obj[key]
        if isinstance(obj,list):
            for index in range(-1,-len(obj)-1,-1):
                try:
                    pickle.dumps(obj[index])
                except:
                    print('>del ',obj[index])
                    del obj[index]
        with open(file_path, 'wb') as f:
            self.prn(f"保存 {obj}")
            pickle.dump(obj,f)

    def load(self, file_path):
        with open(file_path, 'rb+') as f:
            try:
                obj = pickle.load(f)
            except EOFError:
                return None
        return obj
        
    def add_initfunc(self,name,func):
        self.prn(fmt('g')+f"添加initfunc {name}: {func}")
        self.initfunc[name]=func
    def del_initfunc(self,name):
        self.prn(fmt('r')+f"删除initfunc {name}")
        del self.initfunc[name]




