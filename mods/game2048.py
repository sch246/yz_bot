"""Pure 2048 board operations used by live link reactions."""

import random


DIRECTIONS = {
    "w": "up",
    "a": "left",
    "s": "down",
    "d": "right",
    "↑": "up",
    "↓": "down",
    "←": "left",
    "→": "right",
}
d2048 = DIRECTIONS


def fills(value, char, length):
    text = str(value)
    return str(char) * max(0, length - len(text)) + text


def show_mat(matrix) -> str:
    maximum = max(max(line) for line in matrix)
    width = len(str(maximum))
    return "\n".join(
        " ".join(fills(value, "0", width) for value in line)
        for line in matrix
    )


def move_list(values):
    size = len(values)
    compact = [value for value in values if value != 0]
    merged = []
    index = 0
    while index < len(compact):
        if index + 1 < len(compact) and compact[index] == compact[index + 1]:
            merged.append(compact[index] * 2)
            index += 2
        else:
            merged.append(compact[index])
            index += 1
    return merged + [0] * (size - len(merged))


def move_mat(matrix, direction):
    if direction == "left":
        return [move_list(line) for line in matrix]
    if direction == "right":
        return [move_list(line[::-1])[::-1] for line in matrix]
    if direction == "up":
        return list(zip(*[move_list(list(line)) for line in zip(*matrix)]))
    if direction == "down":
        return list(zip(*[move_list(list(line)[::-1])[::-1] for line in zip(*matrix)]))
    return matrix


def get_empty(matrix):
    return [
        (row, column)
        for row, line in enumerate(matrix)
        for column, value in enumerate(line)
        if value == 0
    ]


def rand_if(probability):
    return random.random() <= probability


def setp(matrix, position) -> None:
    row, column = position
    matrix[row][column] = 2 if rand_if(0.75) else 4


def pop_rand(values):
    return values.pop(random.randrange(len(values)))


def step_2048(matrix) -> None:
    setp(matrix, pop_rand(get_empty(matrix)))
