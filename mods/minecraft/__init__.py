"""Trusted Minecraft lifecycle and datapack device configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time


LOAD_AFTER = ("mc", "op", "screen")
CONFIG_PATH = Path("data/device/minecraft.json")


@dataclass(frozen=True)
class Config:
    address: str
    port: int
    password: str
    screen: str
    path: str
    start_command: str
    world_name: str
    pack_format: int


_config: Config | None = None
_connection = None


def _load_config() -> Config:
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Minecraft 设备配置必须是对象")
    config = Config(
        address=value["address"],
        port=value["port"],
        password=value["password"],
        screen=value["screen"],
        path=value["path"],
        start_command=value["start_command"],
        world_name=value["world_name"],
        pack_format=value["pack_format"],
    )
    if not all(
        isinstance(item, str) and item
        for item in (
            config.address,
            config.password,
            config.screen,
            config.path,
            config.start_command,
            config.world_name,
        )
    ):
        raise TypeError("Minecraft 字符串配置必须是非空字符串")
    if isinstance(config.port, bool) or not isinstance(config.port, int):
        raise TypeError("Minecraft RCON 端口必须是整数")
    if isinstance(config.pack_format, bool) or not isinstance(config.pack_format, int):
        raise TypeError("Minecraft pack_format 必须是整数")
    return config


def on_load(_ctx) -> None:
    global _config, _connection
    from mods import mc

    _config = _load_config()
    _connection = mc.MC(_config.address, _config.port, _config.password)


def on_exit() -> None:
    global _connection
    connection, _connection = _connection, None
    if connection is not None:
        connection.close()


def _state():
    if _config is None or _connection is None:
        raise RuntimeError("Minecraft 模块尚未加载")
    return _config, _connection


def _authorized() -> bool:
    from mods import context, op

    return op.is_op(context.current())


def _connect() -> bool:
    _config_value, connection = _state()
    try:
        return connection.connect()
    except OSError:
        return False


def connect_mc() -> bool:
    return _authorized() and _connect()


def _check() -> int:
    config, connection = _state()
    from mods import screen

    try:
        if connection.send("test") != "rcon未连接":
            return 2
    except OSError:
        connection.close()
    if _connect():
        return 1
    return 0 if screen.check(config.screen) else -1


def checkmc():
    return _check() if _authorized() else "无权限"


def start_mc(timeout: int = 120):
    if not _authorized():
        return "无权限"
    config, _connection_value = _state()
    from mods import message, screen

    message.sendmsg("开始启动服务器")
    state = _check()
    if state > 0:
        return "MC已连接"
    if state == -1:
        screen.start(config.screen)
    screen.pop(config.screen)
    screen.send(config.screen, f"cd {config.path} && {config.start_command}")
    elapsed = 0
    rcon_ready = False
    while elapsed < timeout:
        output = screen.pop(config.screen)
        if output:
            message.sendmsg(output)
        if rcon_ready or "RCON running" in output:
            rcon_ready = True
            if _connect():
                return "启动完毕"
        time.sleep(3)
        elapsed += 3
    message.sendmsg("超时")
    return None


def stop_mc(close_screen: bool = False, timeout: int = 30):
    if not _authorized():
        return "无权限"
    config, connection = _state()
    from mods import message, screen

    message.sendmsg("开始关闭服务器")
    state = _check()
    if state == -1:
        return "screen和MC已是关闭状态"
    if state == 0:
        return screen.stop(config.screen) if close_screen else "MC已是关闭状态"
    connection.send("stop")
    connection.close()
    elapsed = 0
    while elapsed < timeout:
        output = screen.pop(config.screen)
        if output:
            message.sendmsg(output)
        if "All dimensions are saved" in output:
            message.sendmsg("All dimensions are saved")
            break
        time.sleep(3)
        elapsed += 3
    if close_screen:
        return screen.stop(config.screen)
    return "MC已关闭"


def restart_mc():
    if not _authorized():
        return "无权限"
    stopped = stop_mc()
    if stopped == "无权限":
        return stopped
    started = start_mc()
    return "重启完毕" if started and started != "无权限" else "失败"


def console(command: str):
    """Send one trusted console command through the configured screen."""
    if not _authorized():
        return "无权限"
    config, _connection_value = _state()
    from mods import screen

    output = screen.send(config.screen, command)
    return "\n".join(
        line[33:].strip() if len(line) > 33 else line.strip()
        for line in output.splitlines()
    )


def datapack_config() -> tuple[str, str, int]:
    config, _connection_value = _state()
    return config.path, config.world_name, config.pack_format
