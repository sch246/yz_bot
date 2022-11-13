
import socket, atexit

from main import mcrcon

sock = None

def connect(host, port, password):
    global sock
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.connect((host, port))
    result = mcrcon.login(sock, password)
    if not result:
        return False
    return True

def send(command):
    global sock
    if sock:
        return mcrcon.command(sock, command)
    else:
        return 'rcon未连接'

def close():
    if sock:
        sock.close()

atexit.register(close)