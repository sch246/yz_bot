"""Trusted Minecraft datapack editing backed by the Minecraft device mod."""

import os
import re

from mods import LATE, context, cq, file, is_available, minecraft, op, text
from mods.command import command


PHASE = LATE
LOAD_AFTER = ("minecraft", "op")

_config = None


def on_load(_ctx):
    global _config
    missing_modules = [name for name in ("minecraft", "op") if not is_available(name)]
    if missing_modules:
        raise RuntimeError("mcf 依赖模块不可用: " + ", ".join(missing_modules))
    mc_path, world_name, pack_format = minecraft.datapack_config()
    if not isinstance(mc_path, str) or not mc_path:
        raise TypeError("mcf.mc_path 必须是非空字符串")
    if not isinstance(world_name, str) or not world_name:
        raise TypeError("mcf.mc_worldname 必须是非空字符串")
    if not isinstance(pack_format, int):
        raise TypeError("mcf.mc_packformat 必须是整数")
    _config = mc_path, world_name, pack_format


class Pack:
    """One Minecraft datapack rooted in the configured world."""

    def __init__(self, name, description="a datapack"):
        mc_path, world_name, pack_format = _config
        pack_root = os.path.join(str(mc_path), str(world_name), "datapacks")
        self.name = name
        self.path = os.path.join(pack_root, name)
        if not os.path.isdir(self.path):
            os.makedirs(self.path, exist_ok=True)
            file.json_write(
                os.path.join(self.path, "pack.mcmeta"),
                {"pack": {"pack_format": pack_format, "description": description}},
            )

    @staticmethod
    def _extension(kind):
        return "mcfunction" if kind == "functions" else "json"

    def _getpath(self, kind, value):
        parts = value.split(":")
        if len(parts) == 1:
            namespace, relative = "minecraft", parts[0]
        elif len(parts) == 2:
            namespace, relative = parts
        else:
            raise ValueError("冒号太多")
        namespace = namespace or "minecraft"
        if not relative:
            raise ValueError("没有名字")
        return os.path.join(
            self.path,
            "data",
            namespace,
            kind,
            relative + "." + self._extension(kind),
        )

    def func_set(self, name, value):
        path = self._getpath("functions", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        file.write(path, value)

    def func_get(self, name):
        return file.read(self._getpath("functions", name))

    def func_del(self, name):
        os.remove(self._getpath("functions", name))

    def tag_func_add(self, function, tag):
        path = self._getpath("tags/functions", tag)
        if os.path.exists(path):
            value = file.json_read(path)
        else:
            value = {"replace": False, "values": []}
        if function not in value["values"]:
            value["values"].append(function)
            file.json_write(path, value)

    def tag_func_del(self, function, tag):
        path = self._getpath("tags/functions", tag)
        if not os.path.exists(path):
            return
        value = file.json_read(path)
        if function in value["values"]:
            value["values"].remove(function)
        if value["values"]:
            file.json_write(path, value)
        else:
            os.remove(path)

    def _execute(self, held):
        header, lines = held
        if not header:
            return
        operation, name, *_ = header.split()
        if operation == "#set":
            self.func_set(name, "\n".join(lines))
        elif operation == "#del":
            self.func_del(name)
        elif operation == "#tagadd":
            for function in lines:
                self.tag_func_add(function, name)
        elif operation == "#tagdel":
            for function in lines:
                self.tag_func_del(function, name)

    def read(self, source):
        held = ["", []]
        for line in source.splitlines():
            if re.match(r"#(?:set|del|tagadd|tagdel) \S", line):
                self._execute(held)
                held = [line, []]
            elif held[0]:
                held[1].append(line.strip())
        self._execute(held)


@command
def run(body: str):
    """创建或修改 Minecraft datapack（管理员）。

    格式：.mcf <数据包名> [描述]，后续正文使用 #set <函数>、#del <函数>、#tagadd <标签>、#tagdel <标签> 分段。
    直接修改设备配置指向的世界数据包目录，需要已加载 Minecraft 设备配置。
    """
    if not op.require_op(context.current()):
        return None
    if not body.strip():
        return run.__doc__
    lines = cq.unescape(body).splitlines()
    first, remaining = lines[0], lines[1:]
    name, description, _ = text.read_params(first, 2)
    Pack(name, description or "a datapack").read("\n".join(remaining))
    return "收到"
