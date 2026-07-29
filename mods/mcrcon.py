"""Minimal Minecraft RCON protocol implementation."""

import collections
import struct


Packet = collections.namedtuple("Packet", ("ident", "kind", "payload"))


class IncompletePacket(Exception):
    def __init__(self, minimum):
        self.minimum = minimum


def decode_packet(data: bytes):
    if len(data) < 14:
        raise IncompletePacket(14)
    length = struct.unpack("<i", data[:4])[0] + 4
    if len(data) < length:
        raise IncompletePacket(length)
    ident, kind = struct.unpack("<ii", data[4:12])
    payload = data[12:length - 2]
    if data[length - 2:length] != b"\x00\x00":
        raise ValueError("RCON packet has invalid padding")
    return Packet(ident, kind, payload), data[length:]


def encode_packet(packet: Packet) -> bytes:
    data = struct.pack("<ii", packet.ident, packet.kind) + packet.payload + b"\x00\x00"
    return struct.pack("<i", len(data)) + data


def receive_packet(sock):
    data = b""
    while True:
        try:
            return decode_packet(data)[0]
        except IncompletePacket as error:
            while len(data) < error.minimum:
                chunk = sock.recv(error.minimum - len(data))
                if not chunk:
                    raise ConnectionError("RCON connection closed mid-packet")
                data += chunk


def send_packet(sock, packet: Packet):
    sock.sendall(encode_packet(packet))


def login(sock, password: str) -> bool:
    send_packet(sock, Packet(0, 3, password.encode("utf-8")))
    return receive_packet(sock).ident == 0


def command(sock, text: str) -> str:
    send_packet(sock, Packet(0, 2, text.encode("utf-8")))
    send_packet(sock, Packet(1, 0, b""))
    response = b""
    while True:
        packet = receive_packet(sock)
        if packet.ident != 0:
            return response.decode("utf-8")
        response += packet.payload
