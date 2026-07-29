"""Link-driven bottle guessing game backed by the current chat storage."""

import random


LOAD_AFTER = ("identity", "message")


def _chat_storage() -> dict:
    from mods import context, identity

    event = context.current()
    if event.get("group_id") is not None:
        return identity.getgroupstorage()
    return identity.getstorage()


def _print(*values) -> None:
    from mods import message

    message.sendmsg(" ".join(map(str, values)))


def bottles_get():
    return _chat_storage().get("bottles", [])


def bottles_answer_get():
    return _chat_storage().get("bottles_answer", [])


def bottles_set(values):
    _chat_storage()["bottles"] = values
    return values


def bottles_answer_set(values):
    _chat_storage()["bottles_answer"] = values
    return values


def _bottles_check() -> int:
    return sum(
        left == right
        for left, right in zip(bottles_get(), bottles_answer_get())
    )


def bottles_check() -> None:
    count = _bottles_check()
    if count == len(bottles_get()):
        _print("瓶子全对了！")
    elif count == 0:
        _print("瓶子全不对！")
    else:
        _print("对了", count, "个！")


def bottles_init(values) -> None:
    if len(values) <= 2:
        _print("瓶子数量至少是3！")
        return
    if len(set(values)) == 1:
        _print("瓶子不能全部一样！")
        return
    bottles = bottles_set(values.copy())
    answer = bottles_answer_set(values.copy())
    random.shuffle(answer)
    while _bottles_check() == len(bottles):
        random.shuffle(answer)
    _print("猜瓶子游戏！")
    bottles_check()


def bottles_guess(values) -> None:
    answer = bottles_answer_get()
    if len(values) != len(answer):
        _print("瓶子数量不对！")
        return
    if set(values) != set(answer):
        _print("瓶子类型不对！")
        return
    bottles_set(values)
    bottles_check()
