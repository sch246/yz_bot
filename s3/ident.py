'''简单地分配id'''

from s3.counter import Counter

class Ident_getter:
    _cycle = set()
    _counter = None
    top = 0
    def __init__(self) -> None:
        self._counter = Counter()
    def get(self):
        if self._cycle:
            return self._cycle.pop()
        else:
            self.top = next(self._counter)
            return self.top
    def ret(self, i:int):
        self._cycle.add(i)
    def is_using(self, i:int):
        if i > self.top or i<0:
            return False
        elif i in self._cycle:
            return False
        return True

class Box:
    '''提供一个容器，在添加对象时向对象提供在容器中删除自己的函数'''
    def __init__(self) -> None:
        self.box = {}
        self.id_getter = Ident_getter()
    def get_iter(self):
        return list(self.box.values())
    def add(self, obj):
        uid = self.id_getter.get()
        self.box[uid]=obj
        def del_self():
            del self.box[uid]
            self.id_getter.ret(uid)
        return del_self
    def get_len(self):
        return len(self.box)