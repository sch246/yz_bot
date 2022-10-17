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
    def using(self, i:int):
        if i > self.top or i<0:
            return False
        elif i in self._cycle:
            return False
        return True