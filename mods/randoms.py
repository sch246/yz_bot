"""Small random helpers shared by commands and the dynamic environment."""

import random


def getran(values: list, ret_idx: bool = False):
    """Return one random item, optionally together with its index."""
    # WHY: ret_idx 只有 cave.Cave.index() 一个调用点。为单个调用点在共享 helper 上开
    # 开关是当时的习惯——优先压低函数总数；现在会另开一个函数。两种都有理由，记在这里
    # 是为了说明它不是被遗忘的通用接口，不必为"还有谁在用"而保留兼容。
    if not values:
        return None
    index = random.randrange(len(values))
    if ret_idx:
        return index, values[index]
    return values[index]


def rd(count: int, sides: int) -> int:
    """Roll ``count`` dice with ``sides`` faces and return their sum."""
    return sum(random.randint(1, sides) for _ in range(count))
