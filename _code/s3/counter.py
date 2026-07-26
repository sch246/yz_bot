'''简单的计数器'''

def Counter():
    '''计数器(生成器)，使用next迭代'''
    value = 0
    while True:
        yield value
        value += 1

def Iter(start=0, func=lambda x: x+1):
    '''迭代器，允许更复杂的迭代'''
    value = start
    while True:
        args = yield value
        if args is None:args=()
        value = func(value, *args)

if __name__=="__main__":
    def test(x, *args):
        print()
        print('x:', x)
        print('args:', args)
        return x+1
    c = Iter(func=test)
    next(c)# 输出初始值
    c.send((1,2,3,))# 输入额外参数
    next(c)
    next(c)
    next(c)
    next(c)
    next(c)