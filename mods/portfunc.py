"""Opt-in encrypted remote-function client/server and key generation.

No socket or remote instance is created at import time.  Trusted device code
must explicitly construct a :class:`Client`, :class:`Server`, or call a
function wrapped by :func:`listen`.
"""

import getpass
import base64
import logging
import os
import socket
import threading
import weakref
from binascii import a2b_hex, b2a_hex

import rsa
from Crypto.Cipher import AES


_logger = logging.getLogger(__name__)
_clients = weakref.WeakSet()
_servers = weakref.WeakSet()
_END = b"msg end"


class VerifyError(Exception):
    pass


def is_valid_ssh_pubkey(pubkey: str) -> bool:
    parts = pubkey.strip().split()
    valid_key_types = {
        "ssh-rsa",
        "ssh-dss",
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ecdsa-sha2-nistp256@openssh.com",
        "sk-ssh-ed25519@openssh.com",
        "ssh-rsa-cert-v01@openssh.com",
        "ssh-dss-cert-v01@openssh.com",
        "ssh-ed25519-cert-v01@openssh.com",
        "ecdsa-sha2-nistp256-cert-v01@openssh.com",
        "ecdsa-sha2-nistp384-cert-v01@openssh.com",
        "ecdsa-sha2-nistp521-cert-v01@openssh.com",
    }
    if len(parts) < 2 or parts[0] not in valid_key_types:
        return False
    try:
        base64.b64decode(parts[1])
    except Exception:
        return False
    return True


def _rsa_size(key) -> int:
    return (key.n.bit_length() + 7) // 8


def _pad(value: str | bytes) -> bytes:
    value = value.encode() if isinstance(value, str) else value
    return value + b"\0" * ((-len(value)) % AES.block_size)


def aes_encrypt(value: str | bytes, key: bytes, iv: bytes) -> bytes:
    return b2a_hex(AES.new(key, AES.MODE_CBC, iv).encrypt(_pad(value)))


def aes_decrypt(value: bytes, key: bytes, iv: bytes, is_str=True):
    result = AES.new(key, AES.MODE_CBC, iv).decrypt(a2b_hex(value)).rstrip(b"\0")
    return result.decode() if is_str else result


def decode(value: bytes, private_key: rsa.PrivateKey, is_str=True):
    size = _rsa_size(private_key)
    key_iv = rsa.decrypt(value[:size], private_key)
    return aes_decrypt(value[size:], key_iv[:16], key_iv[16:], is_str)


def encode(value: str | bytes, public_key: rsa.PublicKey, is_str=True) -> bytes:
    if is_str or not isinstance(value, bytes):
        value = str(value).encode()
    key, iv = os.urandom(16), os.urandom(16)
    return rsa.encrypt(key + iv, public_key) + aes_encrypt(value, key, iv)


def pub_loads(value: str):
    value = value.replace("\\n", "\n").replace("\\s", " ")
    return rsa.PublicKey.load_pkcs1(value.encode())


def pub_dumps(key: rsa.PublicKey) -> str:
    return key.save_pkcs1().decode().replace("\n", "\\n").replace(" ", "\\s")


def priv_loads(value: str):
    return rsa.PrivateKey.load_pkcs1(value.encode())


def priv_dumps(key: rsa.PrivateKey) -> str:
    return key.save_pkcs1().decode()


def load_this_priv(path: str):
    with open(path, encoding="utf-8") as stream:
        name, *lines = stream.read().splitlines()
    return name, priv_loads("\n".join(lines))


def load_pubkeys(path: str) -> dict[str, rsa.PublicKey]:
    result = {}
    with open(path, encoding="utf-8") as stream:
        for raw in stream:
            raw = raw.strip()
            if raw:
                name, value = raw.split(" ", 1)
                result[name] = pub_loads(value)
    return result


def info() -> str:
    return f"{getpass.getuser()}@{socket.gethostname()}"


def genkeys(name: str, path="./"):
    if any(character.isspace() for character in name):
        raise ValueError("name中不能含有空白符")
    public, private = rsa.newkeys(1024)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "rsa.priv"), "w", encoding="utf-8") as stream:
        stream.write(f"{name}\n{priv_dumps(private)}")
    with open(os.path.join(path, "rsa.pub"), "w", encoding="utf-8") as stream:
        stream.write(f"{name} {pub_dumps(public)}\n")


def msg(value: str) -> str:
    lines = value.splitlines()
    return "\n".join(("\\" if index == len(lines) - 1 else "|") + line for index, line in enumerate(lines))


def _send(sock: socket.socket, value: bytes):
    if len(value) % 1024 == 0:
        value += _END
    sock.sendall(value)


def _receive(sock: socket.socket) -> bytes:
    chunks = []
    while True:
        value = sock.recv(1024)
        if not value:
            break
        chunks.append(value)
        if len(value) < 1024:
            break
    result = b"".join(chunks)
    return result[: -len(_END)] if result.endswith(_END) else result


class Client:
    def __init__(self, host, port, name_priv, is_str=True, timeout=None):
        self.is_str = is_str
        self.name, self.private_key = name_priv
        self.socket = socket.create_connection((host, port), timeout=timeout)
        _clients.add(self)
        nonce = os.urandom(16)
        signature = rsa.sign(nonce, self.private_key, "SHA-1")
        self.socket.sendall(signature + nonce + self.name.encode())
        response = self.socket.recv(8192)
        if response == b"0":
            self.close()
            raise KeyError(self.name)
        if response == b"1":
            self.close()
            raise VerifyError(self.name)
        self.public_key = rsa.PublicKey.load_pkcs1(
            decode(response, self.private_key, is_str=False)
        )

    def call(self, value):
        _send(self.socket, encode(value, self.public_key, self.is_str))
        response = _receive(self.socket)
        if not response:
            raise ConnectionError("远端连接已关闭")
        return decode(response, self.private_key, self.is_str)

    def close(self):
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None


connect = Client


class Server:
    def __init__(self, host, port, public_keys, function, backlog=100, is_str=True):
        self.host = host
        self.port = port
        self.public_keys = public_keys
        self.function = function
        self.backlog = backlog
        self.is_str = is_str
        self.socket = None
        self.public_key = None
        self.private_key = None
        self._connections = set()
        self._stopping = threading.Event()
        _servers.add(self)

    def bind(self):
        if self.socket is not None:
            return
        self.public_key, self.private_key = rsa.newkeys(1024)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(self.backlog)
        self.socket = listener

    def serve_forever(self):
        self.bind()
        while not self._stopping.is_set():
            try:
                connection, _ = self.socket.accept()
            except OSError:
                if self._stopping.is_set():
                    return
                raise
            worker = threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name="mods.portfunc.connection",
                daemon=True,
            )
            worker.start()

    def start(self):
        worker = threading.Thread(
            target=self.serve_forever,
            name="mods.portfunc.server",
            daemon=True,
        )
        worker.start()
        return worker

    def _authenticate(self, connection):
        received = connection.recv(8192)
        size = _rsa_size(self.private_key)
        signature, nonce, raw_name = received[:size], received[size : size + 16], received[size + 16 :]
        name = raw_name.decode()
        public_key = self.public_keys.get(name)
        if public_key is None:
            connection.sendall(b"0")
            raise KeyError(name)
        try:
            rsa.verify(nonce, signature, public_key)
        except rsa.VerificationError as error:
            connection.sendall(b"1")
            raise VerifyError(name) from error
        connection.sendall(
            encode(self.public_key.save_pkcs1(), public_key, is_str=False)
        )
        return name, public_key

    def _serve_connection(self, connection):
        self._connections.add(connection)
        try:
            name, public_key = self._authenticate(connection)
            while not self._stopping.is_set():
                request = _receive(connection)
                if not request:
                    return
                value = decode(request, self.private_key, self.is_str)
                result = self.function(value)
                _send(connection, encode(result, public_key, self.is_str))
                _logger.debug("portfunc call completed for %s", name)
        except (KeyError, VerifyError, BrokenPipeError, ConnectionError):
            _logger.exception("portfunc connection ended")
        finally:
            self._connections.discard(connection)
            connection.close()

    def close(self):
        self._stopping.set()
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        for connection in tuple(self._connections):
            connection.close()
        self._connections.clear()


def listen(host, port, public_keys, count=100, is_str=True):
    """Keep the historical decorator shape without starting at decoration time."""
    def decorate(function):
        def run():
            server = Server(host, port, public_keys, function, count, is_str)
            return server.serve_forever()

        return run

    return decorate


def on_exit():
    for client in list(_clients):
        client.close()
    for server in list(_servers):
        server.close()
