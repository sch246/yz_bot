"""Small random helpers shared by commands and the dynamic environment."""

import random


def getran(values: list, ret_idx: bool = False):
    """Return one random item, optionally together with its index."""
    if not values:
        return None
    index = random.randrange(len(values))
    if ret_idx:
        return index, values[index]
    return values[index]


def rd(count: int, sides: int) -> int:
    """Roll ``count`` dice with ``sides`` faces and return their sum."""
    return sum(random.randint(1, sides) for _ in range(count))
