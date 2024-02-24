'''修改对象的源代码，配合重启食用更佳'''
import inspect

class Text:
    def __init__(self, path):
        self.path = path
        with open(self.path, 'r', encoding='utf-8') as file:
            self.lines = file.read().splitlines()

    def __getitem__(self, index):
        if isinstance(index, slice):
            return '\n'.join(self.lines[index])
        return self.lines[index]

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self.lines))
            if isinstance(value, str):
                value = value.splitlines()
            self.lines[start:stop:step] = value
        else:
            self.lines[index] = value
        self.write()

    def write(self):
        with open(self.path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(self.lines))

def get(obj):
    return inspect.getsource(obj)

def set(obj:object,code:str, reload=False):
    path = inspect.getsourcefile(obj)
    if not path: raise IOError('没有找到源文件')
    lines, idx = inspect.getsourcelines(obj)
    Text(path)[idx-1:idx+len(lines)-1] = code.splitlines()
    if reload:
        exit(233)
