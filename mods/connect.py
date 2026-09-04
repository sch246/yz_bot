"""OneBot HTTP ingress and API calls."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import queue
import sys
import threading
from urllib import request

from mods import INFRA


PHASE = INFRA
# This edge is primarily an exit guarantee: stop ingress before storage's
# final save, so no new event can race that save.
LOAD_AFTER = ("log", "scheduler", "storage")

# WHY: 这两个 5 是巧合，不是同一个数——一个是出站 API 的等待上限，一个是入站在一条
# 连接上读完一个请求的上限，两者都是当年试出来的经验值，没人算过。分开命名就是为了
# 别被"统一常量"合成一个，那会让改出站超时悄悄改掉入站行为。
REQUEST_TIMEOUT = 5   # 出站：call_api 等 NapCat 回应
RECV_TIMEOUT = 5      # 入站：一条连接上读完一个请求的上限，超时就断开这条连接

# WHY: 给逐行读 chunk 头留个上限，免得畸形请求把内存读干。数量级抄的是 http.server
# 自己对请求行用的 _MAXLINE，没有别的讲究。
MAX_LINE = 65536

post_url = "http://127.0.0.1"
post_map: dict[str, str | int] = {}

_log = logging.getLogger(__name__)
_events: queue.Queue = queue.Queue()
_server: HTTPServer | None = None
_server_lock = threading.Lock()


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


def _read_body(stream, headers) -> bytes:
    """Read exactly one request body, decoding chunked transfer if it is used.

    WHY: 这十几行看起来像"库应该管的事"，但没有哪个标准库服务端解 chunked 请求体：
    http.server 只把 rfile 原样交给你，wsgiref 也不解(WSGI 规范本身就把 chunked
    推给服务器实现)。所以换库买到的是请求行/头/响应/连接生命周期，不含这一段，它得
    自己写。要删掉它，条件是换成一个真解 chunked 的 HTTP 库(werkzeug 之类)，而不是
    "再等等看 NapCat 会不会改"。

    WHY: 少读一个字节就抛，不返回半个 body。上一版在这里是"读到对端不说话为止"，读少了
    就交给一个从垃圾里捞大括号的兜底，捞不出来就返回 None——**事件静默消失**，不报错
    不留痕，Bot 那次就是没反应。宁可炸在这里，也不要再有那种消失。
    """
    if "chunked" in headers.get("Transfer-Encoding", "").lower():
        body = bytearray()
        while True:
            line = stream.readline(MAX_LINE)
            if not line.strip():
                raise ValueError("chunked body ended without a terminating chunk")
            size = int(line.split(b";", 1)[0], 16)
            if size == 0:
                break
            part = stream.read(size)
            if len(part) != size:
                raise ValueError(f"chunk is short: want {size}, got {len(part)}")
            body += part
            stream.read(2)  # 每块后面那个 CRLF
        # trailer：OneBot 用不到，读掉只是为了不把它剩在连接上。
        while stream.readline(MAX_LINE).strip():
            pass
        return bytes(body)
    return stream.read(int(headers.get("Content-Length") or 0))


class _Ingress(BaseHTTPRequestHandler):
    """Accept one OneBot event per request and hand it to ``recv_msg``.

    WHY: 2022 年起这里是手搓的 socket 解析器，它自己写好了删除条件——"需要真正的
    HTTP 语义(路由、方法、编码协商)时就该换掉，而不是继续往这里加分支"。2026-09-04
    的日志证明条件到了：NapCat 发的是 `Transfer-Encoding: chunked`，没有
    Content-Length，body 长这样 `16f\\r\\n{"self_id":...`。那正是编码协商。
    手搓版对此的反应是两条兜底：没有 Content-Length 就"读到对端不说话为止"，body 不是
    JSON 就"从垃圾里捞最外层大括号"。这两条对短消息碰巧有效——一条消息只有一个块，
    块长度全在 JSON 外面，捞得出来。但 body 一旦跨块，`\\r\\n<hex>\\r\\n` 就夹在 JSON
    中间，捞出来仍然解析失败，那条事件直接消失。也就是说它不是"够用"，是"消息短的
    时候够用"，长消息和带图消息一直在悄悄掉。
    换成 http.server 之后，那两条兜底连同 DRAIN_TIMEOUT 的赌注一起没了必要：长度由
    协议说了算，不再靠等待间隔猜。
    """

    # 一条连接卡住不会永远占着 ingress：超时后库会关掉它(见 handle_one_request)。
    timeout = RECV_TIMEOUT

    # WHY: 明知 NapCat 发的是 HTTP/1.1 keep-alive，也仍然按 1.0 应答、一个请求一条
    # 连接。因为服务器是串行的(见 on_load)：一旦保持长连接，handle() 就会守在那条连接
    # 上等下一个请求，直到 RECV_TIMEOUT，期间别人的事件全在外面排队。这个默认值不是
    # 没升级，是升级的前提是先改成多线程。旧版手搓的也是应答完就 Connection: close，
    # 行为在这一点上没变。
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:
        try:
            body = _read_body(self.rfile, self.headers)
        except (ValueError, OSError) as error:
            _log.warning("OneBot ingress could not read the request body: %s", error)
            self.send_error(400)
            return
        # WHY: 先应答，再入队。旧版是 recv_msg() 里 accept 一个连接、当场解析、当场
        # 返回给 Bot 循环去跑命令，于是 Bot 忙的时候 NapCat 的连接堆在 TCP backlog 里
        # 等着(那个 listen(100) 就是给这个场景准备的)。现在应答只依赖"读完并解析"，
        # 慢命令不再让上游等。代价是队列没有上限：Bot 循环彻底卡死时事件堆在内存里，
        # 而不是被 TCP 挡回去。以这个 Bot 的事件速率不值得加上限，真要加的话，是在
        # Queue(maxsize=...) 上加，并且想清楚满了之后是丢事件还是让上游等。
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "0")
        self.end_headers()
        try:
            event = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            _log.warning("OneBot ingress body is not JSON (head=%r)", bytes(body[:80]))
            return
        if not isinstance(event, dict):
            _log.warning("OneBot ingress body is not a JSON object: %s", type(event).__name__)
            return
        _events.put(event)

    def log_message(self, _format, *_args) -> None:
        # WHY: 库默认把每个请求打到 stderr，绕开 log 模块，也就绕开了那条"# 开头的
        # 输出不进模型上下文"的约定。这里静音，需要报的都由上面几条 warning 报。
        return


def recv_msg() -> dict | None:
    """Block until the next OneBot event arrives; ``None`` means ingress stopped.

    WHY: 队列在这里不是缓冲设计，是转接头：调用方(bot.run / identity 首次配置)是拉
    模型，http.server 是推模型，两者之间总得有一个东西接着。
    """
    if _server is None:
        raise RuntimeError("OneBot listener is not loaded")
    # WHY: 不加超时轮询。CPython 在主线程上的阻塞锁是可被信号打断的，Ctrl-C 照样能
    # 从这里抛出 KeyboardInterrupt(实测过)，所以不需要再引入一个"多久醒一次"的常量。
    return _events.get()


def on_load(ctx) -> None:
    global _server
    with _server_lock:
        if _server is not None:
            return
        # WHY: HTTPServer 而不是 ThreadingHTTPServer——事件必须按到达顺序进队列，
        # 聊天消息乱序是真的 bug，而多线程下入队顺序由调度决定。串行不会成为瓶颈：
        # 这里每个请求只做"读 body + 解析 + 入队"，真正的工作在 bot 循环里。什么时候
        # 该换成多线程：这个 handler 开始做真正的工作的时候。
        # request_queue_size 用库的默认 5 就够，旧版那个 listen(100) 是给"Bot 忙时
        # 连接堆着"准备的，立刻应答之后那个场景不存在了。
        server = HTTPServer(listen, _Ingress)
        threading.Thread(
            target=server.serve_forever, name="onebot-ingress", daemon=True
        ).start()
        _server = server


def on_exit() -> None:
    global _server
    with _server_lock:
        server, _server = _server, None
    if server is not None:
        server.shutdown()
        server.server_close()
    # 有人正卡在 recv_msg() 上的话，放个 None 把它叫醒(调用方本来就要处理 None)。
    _events.put(None)
