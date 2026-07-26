
import socket, atexit

from main import mcrcon


class mc:
    def __init__(self, host, port, password) -> None:
        self.sock = None
        self.host = host
        self.port = port
        self.password = password
        atexit.register(self.close)

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.connect((self.host, self.port))
        result = mcrcon.login(self.sock, self.password)
        if not result:
            return False
        return True

    def send(self, command):
        if self.sock:
            return mcrcon.command(self.sock, command)
        else:
            return 'rcon未连接'

    def close(self):
        if self.sock:
            self.sock.close()
