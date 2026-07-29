"""Trusted source inspection and replacement helpers."""

import inspect


class Text:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as source:
            self.lines = source.read().splitlines()

    def __getitem__(self, index):
        if isinstance(index, slice):
            return "\n".join(self.lines[index])
        return self.lines[index]

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self.lines))
            replacement = value.splitlines() if isinstance(value, str) else value
            self.lines[start:stop:step] = replacement
        else:
            self.lines[index] = value
        self.write()

    def write(self):
        with open(self.path, "w", encoding="utf-8") as target:
            target.write("\n".join(self.lines))


def get(obj) -> str:
    return inspect.getsource(obj)


def set(obj, code: str, reload: bool = False):
    path = inspect.getsourcefile(obj)
    if not path:
        raise OSError("没有找到源文件")
    lines, index = inspect.getsourcelines(obj)
    Text(path)[index - 1:index + len(lines) - 1] = code.splitlines()
    if reload:
        raise SystemExit(233)
