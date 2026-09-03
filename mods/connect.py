"""OneBot HTTP ingress and API calls."""

from __future__ import annotations

import json
import logging
import re
import socket
import sys
import threading
from urllib import request

from mods import INFRA


PHASE = INFRA
# This edge is primarily an exit guarantee: stop ingress before storage's
# final save, so no new event can race that save.
LOAD_AFTER = ("log", "scheduler", "storage")

# WHY: 这两个 5 是巧合，不是同一个数——一个是出站 API 的等待上限，一个是入站读一个
# 请求的上限，两者都是当年试出来的经验值，没人算过。分开命名就是为了别被"统一常量"
# 合成一个，那会让改出站超时悄悄改掉入站行为。
REQUEST_TIMEOUT = 5   # 出站：call_api 等 NapCat 回应
RECV_TIMEOUT = 5      # 入站：读一个请求的头和(带 Content-Length 的)正文

# WHY: 没有 Content-Length 时只能"读到不再有数据为止"，靠这个间隔判断对端说完了。
# 它不是协议，是赌注：body 比这个间隔来得慢就会被截断。实测(0.5s 停顿)旧值 0.2 只收到
# 119/239 字节，截断后大括号不配对，request_to_json 连兜底都捞不出来，于是返回 None
# ——**这条事件被静默丢弃**，Bot 那次就是没反应，既不报错也不留痕。不是伪造成假事件，
# 是干脆消失，所以更难发现。原值对 loopback 通常够用，机器卡顿或 GC 停顿就可能超过。
# 代价只是这条罕见分支每次多等这么久，所以给足。走这条路现在会打日志(见 _recv_request)，
# 据此判断它是否还发生，别再靠猜。
DRAIN_TIMEOUT = 1.0

post_url = "http://127.0.0.1"
post_map: dict[str, str | int] = {}

_log = logging.getLogger(__name__)
_listen_socket: socket.socket | None = None
_socket_lock = threading.Lock()


def _argument(name: str, default: str) -> str:
    arguments = sys.argv[1:]
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError):
        return default


post_port = _argument("-q", "5700")
listen_port = _argument("-p", "5701")
listen = ("127.0.0.1", int(listen_port))


def _target_port(params: dict) -> str | int:
    """Choose one window mapping; a missing ``user_id`` cannot erase a group map."""
    group_id = params.get("group_id")
    if group_id is not None:
        return post_map.get(f"g{group_id}", post_port)
    user_id = params.get("user_id")
    if user_id is not None:
        return post_map.get(f"u{user_id}", post_port)
    return post_port


def call_api(action: str, **params) -> dict:
    body = json.dumps(params).encode("utf-8")
    outgoing = request.Request(
        f"{post_url}:{_target_port(params)}/{action}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(outgoing, timeout=REQUEST_TIMEOUT) as response:
        payload = response.read()
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # WHY: 400 是随手借的 HTTP 语义，不是 OneBot 的 retcode，也不保证与真实
        # retcode 不撞。调用方只区分"retcode == 0"和"其它"，所以撞了也不影响判断；
        # 想区分"NapCat 返回的失败"和"我们没解析出来"的话，得另加字段而不是换这个数。
        return {"retcode": 400, "wording": payload.decode("utf-8", "replace")}
    return value if isinstance(value, dict) else {"retcode": 400, "wording": repr(value)}


def send_msg(msg: str, user_id=None, group_id=None, **params) -> dict:
    if user_id is None and group_id is None:
        raise ValueError("user_id or group_id is required")
    return call_api("send_msg", message=msg, user_id=user_id, group_id=group_id, **params)


_content_length = re.compile(br"Content-Length:\s*(\d+)", re.IGNORECASE)


def _recv_request(client: socket.socket, buffer_size: int = 4096) -> bytes:
    """Read one HTTP request off the socket by hand.

    WHY: 手搓而不是用 http.server，最初只是因为照着教程写、当时也不会别的库。留下来
    是因为对这个 Bot 够用：入站是单点同步的(recv_msg 一次一个)，只需要把一个 POST 的
    body 掏出来，换成框架要多带一套线程模型和生命周期。
    删除条件明确：需要并发接收、或者需要真正的 HTTP 语义(路由、方法、编码协商)时就该
    换掉，而不是继续往这里加分支。
    """
    client.settimeout(RECV_TIMEOUT)
    request = bytearray()
    while b"\r\n\r\n" not in request:
        chunk = client.recv(buffer_size)
        if not chunk:
            return bytes(request)
        request.extend(chunk)
    header, body = bytes(request).split(b"\r\n\r\n", 1)
    match = _content_length.search(header)
    if match:
        expected = int(match.group(1))
        while len(body) < expected:
            chunk = client.recv(min(buffer_size, expected - len(body)))
            if not chunk:
                break
            body += chunk
        body = body[:expected]
    else:
        # 见 DRAIN_TIMEOUT：这条分支是截断风险所在，记一条日志好知道它还发不发生。
        _log.warning("OneBot ingress request has no Content-Length; draining")
        client.settimeout(DRAIN_TIMEOUT)
        try:
            while True:
                chunk = client.recv(buffer_size)
                if not chunk:
                    break
                body += chunk
        except socket.timeout:
            pass
    return header + b"\r\n\r\n" + body


def request_to_json(request: str | bytes) -> dict | None:
    if isinstance(request, str):
        request = request.encode("utf-8")
    separator = request.find(b"\r\n\r\n")
    if separator < 0:
        return None
    body = request[separator + 4:].strip()
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Keep compatibility with the chunked requests accepted by the old
        # listener without implementing an HTTP framework in the Bot.
        #
        # WHY: 这是 go-cqhttp 时代留下的，那时候各家实现都简陋。NapCat 是否还会发出
        # 需要它的请求，现在没人知道——所以这里记一条日志：日志里长期不出现它，就可以
        # 连同 DRAIN_TIMEOUT 那条分支一起删。
        # 它比原意宽(从任何垃圾里捞最外层大括号)，但实测救不了截断的 body：截断处大括号
        # 不配对，捞出来仍然解析失败。所以它放行的是"外面裹了东西的完整 JSON"，
        # 不是"被截断的半个 JSON"——后者一律丢弃。
        _log.warning("OneBot ingress body is not JSON; falling back to brace scan")
        start, end = body.find(b"{"), body.rfind(b"}")
        if start < 0 or end < start:
            return None
        try:
            value = json.loads(body[start:end + 1])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return value if isinstance(value, dict) else None


_response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def recv_msg() -> dict | None:
    server = _listen_socket
    if server is None:
        raise RuntimeError("OneBot listener is not loaded")
    try:
        client, _ = server.accept()
    except OSError:
        if _listen_socket is None:
            return None
        raise
    with client:
        try:
            event = request_to_json(_recv_request(client))
            client.sendall(_response)
            return event
        except socket.timeout:
            _log.warning("OneBot ingress timed out")
        except BrokenPipeError:
            _log.warning("OneBot ingress client closed before acknowledgement")
        except Exception:
            _log.exception("failed to receive OneBot event")
    return None


def on_load(ctx) -> None:
    global _listen_socket
    with _socket_lock:
        if _listen_socket is not None:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(listen)
            # WHY: 100 是任意值。backlog 是"已完成握手但还没 accept 的连接"的排队上限，
            # 而入站只有本机一个 NapCat 在推事件，队列几乎不会有第二个——这个规模下
            # 5 和 100 没有可观察差别。不用查它的来历，也不值得调。
            server.listen(100)
        except Exception:
            server.close()
            raise
        _listen_socket = server


def on_exit() -> None:
    global _listen_socket
    with _socket_lock:
        server, _listen_socket = _listen_socket, None
    if server is not None:
        try:
            server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        server.close()
