'''mcfunction!'''

import os,re

from main import cq,file
# 这部分在pyload里
from .py import mc_path, mc_worldname, mc_packformat, read_params

def run(body:str):
    '''.mcf <pack>
#set <func>
<line>
...

#del <func>

#tagadd <tag>
<func>
...

#tagdel <tag>
<func>
...
'''
    if not body.strip():
        return run.__doc__
    body = cq.unescape(body)
    lines = body.splitlines()
    while len(lines)<2:
        lines.append('')
    first_line, *last_lines = lines

    name, description, _ = read_params(first_line,2)
    Pack(name, description).read('\n'.join(last_lines))
    return '收到'



class Pack:
    '''mc datapck'''
    packdir = os.path.join(mc_path, mc_worldname, 'datapacks')
    def __init__(self, name, description='a datapack') -> None:
        self.name = name
        self.path = os.path.join(Pack.packdir, name)
        if not os.path.isdir(self.path):
            os.mkdir(self.path)
            file.json_write(os.path.join(self.path,'pack.mcmeta'),
                {'pack':
                {'pack_format':mc_packformat,
                'description':description}})
    @staticmethod
    def _get_end(type):
        if type=='functions':
            return 'mcfunction'
        else:
            return 'json'
    def _getpath(self, type:str, path:str):
        lst = path.split(':')
        c = len(lst)
        if c==1:
            namespace = 'minecraft'
            path = path
        elif c==2:
            namespace, path = lst
        else:
            raise ValueError('冒号太多')
        if namespace=='':
            namespace = 'minecraft'
        if path=='':
            raise ValueError('没有名字')
        return os.path.join(self.path, 'data', namespace, type, path+'.'+self._get_end(type))
    def func_set(self, name, value):
        path = self._getpath('functions', name)
        os.makedirs(os.path.split(path)[0],exist_ok=True)
        file.write(path,value)
    def func_get(self, name):
        return file.read(self._getpath('functions', name))
    def func_del(self, name):
        os.remove(self._getpath('functions', name))
    def tag_func_add(self, func:str, tag:str):
        tagpath = self._getpath('tags/functions', tag)
        if not os.path.exists(tagpath):
            J = {'replace':False,'values':[]}
            file.json_write(tagpath,J)
        else:
            J = file.json_read(tagpath)
        if func not in J['values']:
            J['values'].append(func)
            file.json_write(tagpath,J)
    def tag_func_del(self,func:str,tag:str):
        tagpath = self._getpath('tags/functions', tag)
        if not os.path.exists(tagpath):
            return
        J = file.json_read(tagpath)
        if func in J['values']:
            J['values'].remove(func)
        if not J['values']:
            os.remove(tagpath)
        else:
            file.json_write(tagpath,J)

    def _exec(self, hold):
        head, lines = hold
        if head:
            oper, name, *_ = head.split(' ')
            if oper=='#set':
                self.func_set(name, '\n'.join(lines))
            elif oper=='#del':
                self.func_del(name)
            elif oper=='#tagadd':
                for func in lines:
                    self.tag_func_add(func, name)
            elif oper=='#tagdel':
                for func in lines:
                    self.tag_func_del(func, name)

    def read(self, text:str):
        lines = text.splitlines()
        hold = ['',[]]
        for line in lines:
            if not re.match(r'#(set|del|tagadd|tagdel) \S',line):
                if hold[0]:
                    hold[1].append(line.strip())
            else:
                self._exec(hold)
                hold[0] = line
                hold[1] = []
        self._exec(hold)