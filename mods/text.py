"""Pure string parsing and link-template helpers."""

from __future__ import annotations

from collections.abc import Iterable
import re


def addtab(value: str, tab: str = "    ") -> str:
    return tab + re.sub(r"\r?\n|\r", lambda match: match.group() + tab, value)


def deltab(value: str, tab: str = "    ") -> str:
    value = value[len(tab):]
    return re.sub(r"(?:\r?\n|\r)" + re.escape(tab), lambda m: m.group().rstrip(" "), value)


def replace_by_dic(value: str, replacements: dict[str, str]) -> str:
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def replace_by_dic2(value: str, replacements: dict[str, str]) -> str:
    for source, target in replacements.items():
        value = value.replace(target, source)
    return value


_template = re.compile(r"{(\D[^:},]*)?:([^}]+)}|{(\D[^:},]*)}")


def _capture_replacer(loc: dict):
    names: set[str] = set()

    def replace(match: re.Match) -> str:
        if match.group(2) is None:
            name = match.group(3)
            pattern = r"[\S\s]+" if name[0].isupper() else r"\S+"
        else:
            name = match.group(1)
            try:
                value = eval(match.group(2), loc)
                if not value:
                    pattern = ""
                elif isinstance(value, str):
                    pattern = value
                elif isinstance(value, Iterable):
                    pattern = f'({"|".join(map(str, value))})'
                else:
                    pattern = str(value)
            except Exception:
                pattern = match.group(2)
        if not name:
            return pattern
        if name in names:
            return f"(?P={name})"
        names.add(name)
        return f"(?P<{name}>{pattern})"

    return replace


def stc_get(source: str):
    def match(value: str, loc: dict, source: str = source) -> dict | None:
        result = re.match(_template.sub(_capture_replacer(loc), source), value)
        return result.groupdict() if result else None

    return match


def stc_set(target: str):
    def replace(names: dict, target: str = target) -> str:
        for name, value in names.items():
            target = target.replace(f"{{:{name}!r}}", repr(value))
            target = target.replace(f"{{:{name}}}", value)
        return target

    return replace


def stc(value: str, loc: dict, source: str, target: str) -> str | None:
    names = stc_get(source)(value, loc)
    return None if names is None else stc_set(target)(names)


def slice(value: str, start, end) -> str:
    lines = value.splitlines(True) or [""]
    return "".join(lines[start:end])


def stripline(value: str) -> str:
    return "".join(line for line in value.splitlines(True) if line.strip()).strip()


def has_nextline(value: str) -> bool:
    return len((value + "a").splitlines()) > 1


_read = re.compile(r"\s+(\S+)([\S\s]*)")
_read_string = re.compile(r'''\s+('[^']*'|"[^"]*"|\S+)([\S\s]*)''')


def read_params(value: str, count: int = 1, read_str: bool | int = False):
    """Read whitespace-prefixed command parameters, preserving the tail."""
    if count <= 0:
        raise ValueError("count must be greater than zero")
    match = (_read_string if read_str else _read).match(value)
    if not match:
        if not value.strip():
            result = [""] * (count + 1)
            return tuple(result) if count > 1 else (result[0], result[1])
        raise SyntaxError("参数需要以空白符开头")
    item, rest = match.groups()
    if read_str is True and len(item) >= 2 and item[0] in "\"'":
        item = item[1:-1]
    if count == 1:
        return item, rest
    return item, *read_params(rest, count - 1, read_str)


def limit(value: str, length: int) -> str:
    return repr(value[:length]) + "..." if len(value) > length else repr(value)


LASTLINE = "\33[1A\r\33[K"


def is_num(value: str) -> bool:
    if value.startswith(("-", "+")):
        value = value[1:]
    if "." not in value:
        return value.isdigit()
    parts = value.split(".")
    return len(parts) == 2 and (not parts[0] or parts[0].isdigit()) and parts[1].isdigit()


def isfloat(value) -> bool:
    """Keep the historical unsigned decimal predicate used by live links."""
    parts = str(value).split(".")
    return len(parts) <= 2 and all(part.isdigit() for part in parts)


def getint(value: str, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
