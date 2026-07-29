"""Small function-argument adapters kept for dynamic composition."""

from collections.abc import Callable


def curry(function, _args=None, _kwargs=None):
    """Accumulate arbitrary arguments until an empty call invokes the function."""
    args = [] if _args is None else _args
    kwargs = {} if _kwargs is None else _kwargs

    def curried(*more_args, **more_kwargs):
        if not more_args and not more_kwargs:
            return function(*args, **kwargs)
        return curry(
            function,
            [*args, *more_args],
            {**kwargs, **more_kwargs},
        )

    return curried


def preset(function: Callable, param_func: Callable[[list, dict], tuple[list, dict]]):
    def wrapped(*args, **kwargs):
        new_args, new_kwargs = param_func(list(args), kwargs)
        return function(*new_args, **new_kwargs)

    return wrapped


def arg(function: Callable, action: str, *_args, **_kwargs):
    def wrapped(*args, **kwargs):
        values = list(args)
        getattr(values, action)(*_args, **_kwargs)
        return function(*values, **kwargs)

    return wrapped


def kwarg(function: Callable, action: str, *_args, **_kwargs):
    def wrapped(*args, **kwargs):
        getattr(kwargs, action)(*_args, **_kwargs)
        return function(*args, **kwargs)

    return wrapped


def append(function: Callable, _args: list):
    def wrapped(*args, **kwargs):
        return function(*args, *_args, **kwargs)

    return wrapped


def insert(function: Callable, i: int, *_args):
    def wrapped(*args, **kwargs):
        positional = list(args)
        positional[i:i] = _args
        return function(*positional, **kwargs)

    return wrapped


def update(function: Callable, dic: dict):
    def wrapped(*args, **kwargs):
        kwargs.update(dic)
        return function(*args, **kwargs)

    return wrapped


def setl(values: list, value):
    values.append(value)
    return value
