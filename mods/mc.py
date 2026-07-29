"""Lazy Minecraft RCON connections for trusted device functions."""

import socket
import weakref

from mods import mcrcon


_connections = weakref.WeakSet()


class MC:
    def __init__(self, host, port, password):
        self.sock = None
        self.host = host
        self.port = port
        self.password = password
        _connections.add(self)

    def connect(self) -> bool:
        self.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.connect((self.host, self.port))
            if not mcrcon.login(sock, self.password):
                sock.close()
                return False
        except BaseException:
            sock.close()
            raise
        self.sock = sock
        return True

    def send(self, command):
        if self.sock is None:
            return "rcon未连接"
        return mcrcon.command(self.sock, command)

    def close(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            sock.close()


mc = MC


def on_exit():
    for connection in list(_connections):
        try:
            connection.close()
        except OSError:
            pass
