

def is_inheritable(cls):
    try:
        class TestSubclass(cls):
            pass
        return True
    except TypeError:
        return False

def AttrWrapper(obj):
    if hasattr(obj, '__dict__'):
        attributes = {**obj.__dict__}
    elif hasattr(obj, '__slots__'):
        attributes = {attr: getattr(obj, attr) for attr in obj.__slots__ if hasattr(obj, attr)}
    else:
        attributes = {}
    _type = type(obj)
    if not is_inheritable(_type): # 如果不是type的实例，那么不可继承
        _type = object
    class Wrapper(_type):
        def __getattr__(self, name):
            if name == '__dict__':
                return attributes
            elif name in attributes:
                return attributes[name]
            return getattr(obj, name)

        def __setattr__(self, name, value):
            if name in attributes:
                attributes[name] = value
            else:
                setattr(obj, name, value)
    return Wrapper()

def add_attr(obj, attributes, overwrite=False):
    '''
    警告：返回的对象的可变属性会影响原对象

    默认会创建一个包裹对象的对象，类型会继承，可以使用__dict__来进一步修改它的属性

    若 overwrite 为 True，则尝试修改原本的对象的属性
    如果修改不了则会报 RuntimeError
    '''
    if not overwrite:
        obj = AttrWrapper(obj)
    if hasattr(obj, '__dict__'):
        obj.__dict__.update(attributes)
        return obj
    try:
        for name, attr in attributes.items():
            setattr(obj, name, attr)
        return obj
    except Exception as e:
        raise RuntimeError(f'修改对象的属性时出现了异常: {e}')