"""Opt-in attribute wrapping for the trusted dynamic environment."""


def is_inheritable(cls) -> bool:
    try:
        class TestSubclass(cls):
            pass
    except TypeError:
        return False
    return True


def AttrWrapper(obj):
    if hasattr(obj, "__dict__"):
        attributes = dict(obj.__dict__)
    elif hasattr(obj, "__slots__"):
        attributes = {
            name: getattr(obj, name)
            for name in obj.__slots__
            if hasattr(obj, name)
        }
    else:
        attributes = {}

    base = type(obj)
    if not is_inheritable(base):
        base = object

    class Wrapper(base):
        def __getattr__(self, name):
            if name == "__dict__":
                return attributes
            if name in attributes:
                return attributes[name]
            return getattr(obj, name)

        def __setattr__(self, name, value):
            if name in attributes:
                attributes[name] = value
            else:
                setattr(obj, name, value)

    return Wrapper()


def add_attr(obj, attributes: dict, overwrite: bool = False):
    """Add attributes directly or through a transparent wrapper."""
    target = obj if overwrite else AttrWrapper(obj)
    if hasattr(target, "__dict__"):
        target.__dict__.update(attributes)
        return target
    try:
        for name, value in attributes.items():
            setattr(target, name, value)
    except Exception as error:
        raise RuntimeError(f"修改对象的属性时出现了异常: {error}") from error
    return target
