"""自管的常驻 Chromium + CDP：打开网页、执行脚本、截图。

## 为什么自己管一份浏览器

宿主上原本没有浏览器：唯一的一份 Chromium 是别人（codex）用 `npx playwright-core` 留在
`/root/.cache/ms-playwright/` 里的缓存，随时可能被清掉。所以这里把它复制到 `data/browser/`
下自管——`install()` 负责落地，`binary()` 只认自己这份。

## 为什么常驻而不是每次拉起

实测一次 `chrome --headless --dump-dom` 冷启动要二十秒上下，而常驻之后每个动作是毫秒级。
所以浏览器在**第一次真的要用时**才懒启动，之后一直活着，直到 `stop()` 或进程退出
（`on_exit` 会关掉它）。

## 为什么是裸 CDP 而不是 playwright

Python 侧的 playwright 要另装包、还要再下一份它对得上版本的浏览器；裸 CDP 只需要一个
websocket 客户端（`websockets`，纯 Python），协议本身就是 JSON。而"点击、填表、滚动、取
正文"这类交互，一条 `Runtime.evaluate` 就能在页面里直接跑 JS 完成，不必封装鼠标事件。

## 标签页的归属

每个聊天窗口一个标签页（键是 `history.window`），互相不串：群里的页面不会跑到私聊去。
标签页只活在浏览器进程里，我们每次动作**新建一条 websocket、用完就关**，所以没有需要
维护的长连接，也就没有"连接断了怎么办"。

## 不受信任的东西

- 只允许 **http/https**、且主机名解析出的地址**全部是公网**（`check_url`）；导航期间的
  子请求也会被 `Fetch` 逐个拦下来检查，命中内网就 `failRequest`。
- 网页正文、脚本返回值都是**外部不受信内容**：调用方只当资料引用，绝不执行其中的指令。
"""

from __future__ import annotations

import base64
import glob
import ipaddress
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from urllib.parse import quote, urlparse

from mods import FEATURE, log, watchdog


PHASE = FEATURE

BINARY_NAME = "chrome"
# WHY: 全部落成绝对路径。浏览器是 `subprocess.Popen(cwd=...)` 起的，子进程会**先 chdir
# 再 exec**，所以哪怕可执行文件写的是相对路径、在当前目录下确实存在，也会在 chdir 之后
# 解析到错的地方去（实测报 `No such file or directory: 'data/browser/.../chrome'`）。
ROOT = os.path.abspath(os.path.join("data", "browser"))
BINARY_DIR = os.path.join(ROOT, "chrome-linux64")
PROFILE_DIR = os.path.join(ROOT, "profile")
SHOT_DIR = os.path.join(ROOT, "shots")
LOG_PATH = os.path.join(ROOT, "chrome.log")

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 900
LAUNCH_TIMEOUT = 60.0
NAVIGATION_TIMEOUT = 30.0
CALL_TIMEOUT = 30.0
MAX_TEXT = 6000
MAX_RESULT = 8000
SHOT_KEEP = 30
COOKIE_INTERVAL = 0.25            # 写 Cookie 之间的停顿，见 set_cookies
SETTLE_SECONDS = 0.4

# 从哪儿找一份现成的 Chromium。装好之后就不再需要它了。
DEFAULT_SOURCES = (
    "/root/.cache/ms-playwright/chromium-*/chrome-linux64",
    "~/.cache/ms-playwright/chromium-*/chrome-linux64",
    "~/.cache/puppeteer/chrome/*/chrome-linux64",
)

_stream = log.stream("browser")

_state_lock = threading.RLock()
_page_lock = threading.RLock()
_process: subprocess.Popen | None = None
_port = 0
_pages: dict[tuple, str] = {}
# 主机名 -> 检查结论。只放**确定**的结论，见 _public_host。
_host_cache: dict[str, str | None] = {}


# --------------------------------------------------------------------------- 安装


def _directory_size(path: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def _locate_source(source: str = "") -> str | None:
    """找一份可用的 Chromium 目录：显式给的优先，否则扫已知缓存位置，取版本最高的。"""
    if source:
        candidates = [source]
    else:
        candidates = []
        for pattern in DEFAULT_SOURCES:
            candidates.extend(sorted(glob.glob(os.path.expanduser(pattern))))
    for candidate in reversed(candidates):
        if os.path.isfile(os.path.join(candidate, BINARY_NAME)):
            return candidate
    return None


def install(source: str = "") -> str:
    """把一份 Chromium 复制到 `data/browser/` 下自管，返回结果说明。

    @param
    source: 源目录（里面要有 `chrome`）；留空则自动扫已知的缓存位置
    """
    origin = _locate_source(source)
    if origin is None:
        raise RuntimeError("找不到可用的 Chromium 源目录，请用 source= 指定一个含 chrome 的目录")
    if os.path.abspath(origin) == os.path.abspath(BINARY_DIR):
        return f"浏览器已就位：{BINARY_DIR}"
    if _running():
        stop("重新安装浏览器")
    os.makedirs(ROOT, exist_ok=True)
    shutil.rmtree(BINARY_DIR, ignore_errors=True)
    shutil.copytree(origin, BINARY_DIR, symlinks=True)
    target = os.path.join(BINARY_DIR, BINARY_NAME)
    os.chmod(target, 0o755)
    megabytes = _directory_size(BINARY_DIR) / 1048576
    _stream.info("浏览器已安装：%s -> %s（%.0f MB）", origin, BINARY_DIR, megabytes)
    return f"浏览器已安装到 {BINARY_DIR}（{megabytes:.0f} MB，源：{origin}）"


def binary() -> str:
    """自管 Chromium 的可执行文件路径；没装就报错。"""
    path = os.path.join(BINARY_DIR, BINARY_NAME)
    if not os.path.isfile(path):
        raise RuntimeError(f"浏览器还没装：{path} 不存在，先调用 install()")
    return path


# --------------------------------------------------------------------------- 进程


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _endpoint(port: int, path: str, method: str = "GET", timeout: float = 3.0):
    """问一句 HTTP 版的 DevTools 接口（`/json/version`、`/json/list`、`/json/new`）。

    WHY: 这里**不能**假设回应是 JSON：`/json/close/<id>` 按设计回一行纯文本
    （"Target is closing"），硬解会把一次成功的关闭报成"关闭遗留标签页失败"，每次启动
    浏览器都在日志里留两条假 traceback。解不出来就原样把文本交回去，调用方本来就都在
    用 `isinstance` 挑自己认的类型。
    """
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _devtools(port: int, timeout: float = 1.0) -> dict | None:
    if not port:
        return None
    try:
        value = _endpoint(port, "/json/version", timeout=timeout)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _running() -> bool:
    return _process is not None and _process.poll() is None


def _stale_pids() -> list[int]:
    """列出仍在用我们这份 profile、但已经不被跟踪的 Chrome 进程。

    WHY: 需要它是因为"跟踪"可能断掉——进程被强杀、模块被重载、或者上一次运行留下的孤儿。
    残留进程会一直占着 profile 目录和调试端口，而 Chrome 的单例锁又会让新实例起不来（或
    更糟：两个实例共用一份 profile）。所以启动前先按 `--user-data-dir` 认出它们并清掉。
    """
    target = f"--user-data-dir={PROFILE_DIR}"
    tracked = _process.pid if _running() else 0
    mine = os.getpid()
    found: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == mine or pid == tracked:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        # WHY: 不能用 `split(b"\0")` 认参数——Chrome 会把自己的 argv 就地改写成**空格**
        # 分隔的长串（为了 `ps` 输出好看），NUL 分隔在它身上已经不存在，按 NUL 切只会拿到
        # 一整行、于是永远认不出残留进程。统一把 NUL 换成空格再切词，两种形态都对。
        tokens = raw.replace(b"\0", b" ").decode("utf-8", "replace").split()
        if not tokens or BINARY_NAME not in tokens[0]:
            continue
        # WHY: 子进程（zygote / renderer / gpu / crashpad）会**继承**父进程的命令行开关，
        # 包括 --user-data-dir，于是它们也会命中下面的判断。它们不是残留——主进程一死它们
        # 跟着死；把它们算进来会让 status() 谎报"有 N 个残留"，更糟的是 _reap_stale() 会去
        # SIGTERM 一个活得好好的浏览器的那堆子进程。主进程与子进程的可靠区分就是后者带
        # --type=，前者不带。
        if any(token.startswith("--type=") for token in tokens):
            continue
        if target in tokens:
            found.append(pid)
    return found


def _reap_stale() -> int:
    """清掉无主的 Chrome 进程，返回清掉的数量。先 SIGTERM 再 SIGKILL。"""
    pids = _stale_pids()
    if not pids:
        return 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _stale_pids():
        time.sleep(0.1)
    for pid in _stale_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _stream.info("清掉 %d 个无主的 Chrome 进程：%s", len(pids), pids)
    return len(pids)


def _clear_profile_locks() -> None:
    """上一个 Chrome 被硬杀之后，profile 里会留下单例锁，不清理就起不来。"""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = os.path.join(PROFILE_DIR, name)
        try:
            if os.path.islink(path) or os.path.exists(path):
                os.remove(path)
        except OSError:
            _stream.info("清理 %s 失败", path, exc_info=True)


def _launch() -> None:
    global _process, _port
    if _running():
        _terminate(_process)
    executable = binary()
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(SHOT_DIR, exist_ok=True)
    _reap_stale()
    _clear_profile_locks()
    port = _free_port()
    command = [
        executable,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-extensions",
        "--disable-features=Translate,MediaRouter",
        "--disable-breakpad",
        "--hide-scrollbars",
        "--mute-audio",
        "--password-store=basic",
        "--use-mock-keychain",
        f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}",
        f"--user-data-dir={PROFILE_DIR}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        "about:blank",
    ]
    stream = open(LOG_PATH, "ab", buffering=0)
    # WHY: 浏览器是常驻进程，不属于任何一次工具执行——不摘出来，第一次访问网页时它就
    # 被记进那次调用的登记表，一个 ^C 就把它连同页面一起带走。
    with watchdog.detached():
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
        )
    deadline = time.monotonic() + LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"浏览器刚启动就退出了（退出码 {process.returncode}），日志见 {LOG_PATH}")
        if _devtools(port) is not None:
            _process, _port = process, port
            _pages.clear()
            _fresh_target(port)
            _stream.info("浏览器已启动：pid=%s port=%s", process.pid, port)
            return
        time.sleep(0.2)
    _terminate(process)
    raise RuntimeError(f"浏览器 {LAUNCH_TIMEOUT:.0f} 秒内没有就绪，日志见 {LOG_PATH}")


def _wait_exit(process, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.1)
    return process.poll() is not None


def _terminate(process) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            if not _wait_exit(process, 5.0):
                process.kill()
    except Exception:
        _stream.info("关闭浏览器进程失败", exc_info=True)


def start() -> dict:
    """确保浏览器在跑，返回 `{"port", "pid", "started"}`。"""
    with _state_lock:
        if _running() and _devtools(_port) is not None:
            return {"port": _port, "pid": _process.pid, "started": False}
        _launch()
        return {"port": _port, "pid": _process.pid, "started": True}


def stop(reason: str = "") -> str:
    """关掉浏览器进程，返回结果说明。"""
    global _process, _port
    with _state_lock:
        process, port = _process, _port
        _process, _port = None, 0
        _pages.clear()
    if process is None or process.poll() is not None:
        return "浏览器本来就没在跑"
    uri = _browser_ws(port)
    if uri:
        try:
            with _Session(uri) as session:
                session.call("Browser.close", timeout=5.0)
        except Exception:
            _stream.info("请 Chrome 自己退出失败，改为杀进程", exc_info=True)
    if not _wait_exit(process, 5.0):
        _terminate(process)
    # WHY: 主进程退出不一定带走全部子进程（zygote / renderer 可能变成孤儿），所以再按
    # profile 认一遍，确保没有残留继续占着目录和端口。
    leftovers = _reap_stale()
    _stream.info("浏览器已关闭（%s）", reason or "未说明原因")
    return f"浏览器已关闭（顺带清掉 {leftovers} 个残留进程）" if leftovers else "浏览器已关闭"


def on_exit() -> None:
    stop("模块退出")


# --------------------------------------------------------------------------- 地址检查


def _parse_ip(value: str):
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return ipaddress.IPv4Address(parsed.ipv4_mapped)
    return parsed


def _resolve_public(host: str) -> tuple[str | None, bool]:
    """检查一个主机名，返回 ``(不通过的理由或 None, 这个结论确不确定)``。

    WHY: 结论分"确定"和"不确定"两种，因为只有前者可以被缓存。由**具体地址**得出的判断
    是确定的：字面 IP 的性质、以及解析出地址之后对那些地址的判断，都不会因为再问一次而
    改变。而 `gaierror`、没解析出地址、地址认不出来，说的是"这次没问出来"，不是主机的
    性质——把它们记住，一次 DNS 抖动就会把某个域名钉死到进程重启，`.reboot` 成了 DNS
    抖动的修法。见 _public_host。
    """
    literal = _parse_ip(host)
    if literal is not None:
        return (None if literal.is_global else f"地址不对外（{host}）"), True
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        return f"域名解析失败（{error}）", False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return "域名没有解析出地址", False
    for address in addresses:
        parsed = _parse_ip(address.split("%")[0])
        if parsed is None:
            return f"地址无法识别（{address}）", False
        if not parsed.is_global:
            return f"解析到不对外地址（{address}）", True
    return None, True


def _public_host(host: str) -> str | None:
    """主机名不全是公网就返回理由，通过返回 None。

    WHY: 缓存只收**确定**的结论（见 _resolve_public），所以它不是一个纯粹按主机名记结果
    的 cache：解析失败那一类每次都会重新问一次。代价是一次本地解析，换掉的是"网络抖一下
    就把一个域名永久拉黑"。

    WHY: 缓存**不过期**，而且检查与浏览器自己的解析之间必然有时间差（Chromium 不共享这
    次结果，自己再解析一遍），所以 DNS rebinding 这条路只在第一次被拦住。这是明知接受的：
    能靠它拿到的是宿主上的内网服务，而同一个模型手上的 exec_code 和 host 本来就能直接
    读写这台机器，边际风险接近零。要改的话该改的是信任模型，不是在这里加一个 TTL 假装
    挡住了。
    """
    if not host:
        return "缺少主机名"
    key = host.strip("[]").lower()
    if key in _host_cache:
        return _host_cache[key]
    reason, settled = _resolve_public(key)
    if settled:
        _host_cache[key] = reason
    return reason


def check_url(url: str) -> str:
    """规范化并检查一个待访问地址，返回可用的 URL，不通过就抛 `ValueError`。

    @param
    url: 完整地址；没写协议时按 https 补
    """
    candidate = (url or "").strip()
    if not candidate:
        raise ValueError("URL 不能为空")
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"只允许 http/https 地址，收到 {scheme or '空协议'}")
    host = parsed.hostname
    if not host:
        raise ValueError("地址里没有主机名")
    reason = _public_host(host)
    if reason:
        raise ValueError(f"拒绝访问 {host}：{reason}")
    return candidate


# --------------------------------------------------------------------------- CDP


class _Session:
    """一条通往某个标签页的 CDP 连接；用完即关。"""

    def __init__(self, uri: str) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError as error:
            raise RuntimeError("缺少 websockets 依赖：pip install websockets") from error
        self._ws = connect(uri, max_size=None, open_timeout=15.0, close_timeout=3.0, proxy=None)
        self._counter = 0
        self._pending: dict[int, dict] = {}
        # WHY: 事件必须**始终**走同一个出口。最初只在某一次调用上挂回调，别的调用（包括
        # 回调内部自己发起的那些）收到事件就顺手丢掉——于是导航期间被 `Fetch` 拦下的请求
        # 没人放行、页面永远打不开；`Page.loadEventFired` 也可能被吃掉，让"是否加载完成"
        # 干等到超时。现在处理器挂在连接上，任何一次等待都会把事件交给它。
        self.handler = None

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *_error) -> None:
        self.close()

    def call(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT) -> dict:
        """发一条 CDP 命令并等它的回应，途中收到的事件交给 `handler`。"""
        self._counter += 1
        ident = self._counter
        self._ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            # WHY: 每一次循环都要重查暂存，不能只在进函数时查一次。回调里发的命令是**嵌套**
            # 的：外层正在等自己的回应，`Fetch.requestPaused` 的处理里又调一次 `call`，那次
            # 读到的第一条就可能是外层的回应——它按 id 暂存，外层却已经错过检查点，于是永远
            # 等一条**已经到过**的消息，直到超时。一页并发几十个子请求时所有层一起过期，
            # 就是"放行/拦截请求失败"刷屏 + open_page 报「等 CDP 回应超时」（2026-09-18
            # m.dianping.com）。放在循环顶上是安全的：`_dispatch` 返回后一定重新检查一次。
            stashed = self._pending.pop(ident, None)
            if stashed is not None:
                return self._settle(stashed, ident)
            message = self._receive(deadline)
            if "id" in message:
                if message.get("id") == ident:
                    return self._settle(message, ident)
                # WHY: 回调里会再发命令（`Fetch.requestPaused` 要先放行才能继续），所以不是
                # 我要的回应不能丢，缓存起来等对应那次调用自己来取。
                self._pending[message.get("id")] = message
                continue
            self._dispatch(message)

    def wait_until(self, predicate, timeout: float) -> bool:
        """一边收事件一边等 *predicate* 成立；超时返回 False。

        WHY: 等的是一个**状态**而不是某一条事件。`Page.loadEventFired` 可能在开始等之前
        就已经来过（导航很快时就是这样），只等事件会白等到超时；由处理器把它变成状态位，
        这里再查状态，两种时序都对。
        """
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._dispatch(self._receive(deadline))

    def _dispatch(self, message: dict) -> None:
        handler = self.handler
        if handler is not None:
            handler(message)

    def _settle(self, message: dict, ident: int) -> dict:
        error = message.get("error")
        if error:
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"CDP 命令失败：{detail or error}")
        return message.get("result") or {}

    def _receive(self, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("等 CDP 回应超时")
        try:
            raw = self._ws.recv(timeout=remaining)
        except TimeoutError as error:
            raise TimeoutError("等 CDP 回应超时") from error
        return json.loads(raw)


def _list_targets(port: int) -> list[dict]:
    try:
        value = _endpoint(port, "/json/list", timeout=3.0)
    except Exception:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _new_target(port: int) -> dict | None:
    try:
        value = _endpoint(port, f"/json/new?{quote('about:blank', safe='')}", method="PUT", timeout=8.0)
    except Exception:
        _stream.info("新建标签页失败", exc_info=True)
        return None
    return value if isinstance(value, dict) else None


def _page_key() -> tuple:
    """当前动作属于哪个窗口；没有路由信息时所有无名调用共用一个标签页。"""
    try:
        from mods import context, history

        event = context.current()
        if isinstance(event, dict) and event:
            window = history.window(event)
            if window is not None:
                return window
    except Exception:
        pass
    return ("shared",)


def _fresh_target(port: int) -> dict | None:
    """关掉启动时已经存在的标签页，再新建一张空白页。

    WHY: Chrome 会按 profile 恢复上一次的会话，于是"刚起来的浏览器"里可能已经有上一轮留下的
    页面。`_page_target` 允许认领无主标签页（这是为了复用初始的 about:blank），于是第一个
    动浏览器的窗口可能认领到一个**别人上一次留下的页面**——它看到的"当前页面"根本不是它开的。
    启动时统一清空，让每次启动都是确定的一页空白，认领机制就只会在空白的 about:blank 上生效。
    """
    for target in _list_targets(port):
        if target.get("type") != "page":
            continue
        try:
            _endpoint(port, f"/json/close/{target.get('id')}", timeout=3.0)
        except Exception:
            _stream.info("关闭遗留标签页失败", exc_info=True)
    return _new_target(port)


def _page_target(port: int, key: tuple, create: bool = True) -> dict | None:
    targets = _list_targets(port)
    owned = {target_id for target_id in _pages.values()}
    known = _pages.get(key)
    if known:
        for target in targets:
            if target.get("id") == known and target.get("webSocketDebuggerUrl"):
                return target
        _pages.pop(key, None)
    if not create:
        return None
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl") and target.get("id") not in owned:
            _pages[key] = target["id"]
            return target
    target = _new_target(port)
    if target is None or not target.get("webSocketDebuggerUrl"):
        return None
    _pages[key] = target.get("id")
    return target


def _browser_ws(port: int) -> str:
    version = _devtools(port) or {}
    return version.get("webSocketDebuggerUrl") or ""


def _metrics(height: int = VIEWPORT_HEIGHT) -> dict:
    return {
        "width": VIEWPORT_WIDTH,
        "height": height,
        "deviceScaleFactor": 1,
        "mobile": False,
    }


def _evaluate(session: _Session, expression: str, timeout: float = CALL_TIMEOUT):
    result = session.call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": True,
        },
        timeout=timeout,
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(_exception_text(result["exceptionDetails"]))
    return (result.get("result") or {}).get("value")


def _exception_text(details: dict) -> str:
    value = details.get("exception") or {}
    description = value.get("description") or value.get("value")
    text = str(description or details.get("text") or "页面脚本抛出了异常")
    return text.splitlines()[0][:400]


def _clip(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit] + f"\n…（已截断，原长 {len(value)} 字符）"


def _blocking_handler(session: _Session, blocked: list[str], loaded: threading.Event):
    """导航期间把每个请求过一遍公网检查，并把"加载完成"变成一个状态位。

    命中内网或非 http 目标就让这个请求失败（`failRequest`），其余一律放行。注意**每个**
    被拦下的请求都必须有下文（放行或失败），少回一个，页面就停在那里等它。
    """

    def handle(message: dict) -> None:
        method = message.get("method")
        if method == "Page.loadEventFired":
            loaded.set()
            return
        if method != "Fetch.requestPaused":
            return
        params = message.get("params") or {}
        request_id = params.get("requestId")
        url = (params.get("request") or {}).get("url") or ""
        if not request_id:
            return
        try:
            parsed = urlparse(url)
            if parsed.scheme.lower() not in {"http", "https"}:
                reason = f"协议不受支持（{parsed.scheme}）"
            else:
                reason = _public_host(parsed.hostname or "")
        except Exception:
            # WHY: 检查本身出错时放行。主文档在 check_url 里已经严格拦过一次，这里是第二道
            # 网；为了它把整页子资源全打死，会把"偶尔解析慢"变成"网页打不开"。
            reason = None
        try:
            if reason:
                blocked.append(f"{url}（{reason}）")
                session.call(
                    "Fetch.failRequest",
                    {"requestId": request_id, "errorReason": "AddressUnreachable"},
                    timeout=8.0,
                )
            else:
                session.call("Fetch.continueRequest", {"requestId": request_id}, timeout=8.0)
        except Exception:
            _stream.info("放行/拦截请求失败", exc_info=True)

    return handle


# --------------------------------------------------------------------------- 动作


_JS_STATE = (
    "(() => ({"
    "title: document.title,"
    "url: location.href,"
    "ready: document.readyState,"
    "chars: document.body && document.body.innerText ? document.body.innerText.length : 0"
    "}))()"
)

_JS_LINKS = (
    "(() => {"
    "const out = []; const seen = new Set();"
    "for (const anchor of document.querySelectorAll('a[href]')) {"
    "const href = anchor.href;"
    "if (!href || seen.has(href)) continue;"
    "seen.add(href);"
    "const label = (anchor.innerText || anchor.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');"
    "out.push({text: label.slice(0, 80), href: href});"
    "if (out.length >= 400) break;}"
    "return out;})()"
)


def open_page(url: str, timeout: float = NAVIGATION_TIMEOUT) -> dict:
    """打开（或跳转到）当前窗口的标签页，返回页面状态。

    @param
    url: 目标地址，http/https
    timeout: 等页面加载完成的秒数上限
    """
    target_url = check_url(url)
    port = start()["port"]
    with _page_lock:
        target = _page_target(port, _page_key())
        if target is None:
            raise RuntimeError("拿不到可用的标签页")
        blocked: list[str] = []
        loaded = threading.Event()
        with _Session(target["webSocketDebuggerUrl"]) as session:
            session.handler = _blocking_handler(session, blocked, loaded)
            session.call("Page.enable")
            session.call("Emulation.setDeviceMetricsOverride", _metrics())
            session.call(
                "Fetch.enable",
                {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
            )
            try:
                session.call("Page.navigate", {"url": target_url})
                arrived = session.wait_until(loaded.is_set, timeout)
            finally:
                try:
                    session.call("Fetch.disable", timeout=8.0)
                except Exception:
                    pass
                session.handler = None
            if arrived:
                # WHY: load 之后还有一批 SPA 会异步把首屏渲染出来，等一下再读，拿到的
                # 正文通常从"空壳"变成"有内容"。
                time.sleep(SETTLE_SECONDS)
            state = _state(session)
    state["loaded"] = arrived
    state["blocked"] = blocked
    return state


def _state(session: _Session) -> dict:
    value = _evaluate(session, _JS_STATE)
    if not isinstance(value, dict):
        return {"title": "", "url": "", "ready": "", "chars": 0}
    return {
        "title": str(value.get("title") or ""),
        "url": str(value.get("url") or ""),
        "ready": str(value.get("ready") or ""),
        "chars": int(value.get("chars") or 0),
    }


def _with_page(action, create: bool = False):
    """在"当前窗口的标签页"上做一件事：拿不到标签页就返回 None。"""
    port = start()["port"]
    with _page_lock:
        target = _page_target(port, _page_key(), create=create)
        if target is None:
            return None
        with _Session(target["webSocketDebuggerUrl"]) as session:
            return action(session)


def page_text(limit: int = MAX_TEXT) -> str:
    """当前页面渲染出来的纯文本（按 *limit* 截断）。

    @param
    limit: 最多返回多少字符
    """
    value = _with_page(lambda session: _evaluate(session, "document.body ? document.body.innerText : ''"))
    return _clip(str(value).strip(), limit) if isinstance(value, str) else ""


def page_links(limit: int = 60) -> list[dict]:
    """当前页面的链接清单，每项 `{"text", "href"}`。

    @param
    limit: 最多返回多少条
    """
    value = _with_page(lambda session: _evaluate(session, _JS_LINKS))
    if not isinstance(value, list):
        return []
    links: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "")
        if not href.lower().startswith(("http://", "https://")):
            continue
        links.append({"text": str(item.get("text") or ""), "href": href})
        if limit > 0 and len(links) >= limit:
            break
    return links


def run_js(code: str, timeout: float = CALL_TIMEOUT) -> dict:
    """在当前页面里跑一段脚本，返回 `{"ok": true, "value"}` 或 `{"ok": false, "error"}`。

    @param
    code: 一段 JS 表达式；要拿 DOM 自己 `return`/用 IIFE，返回值需要能转成 JSON
    timeout: 等结果的秒数上限
    """
    def action(session: _Session) -> dict:
        value = _evaluate(session, code, timeout)
        if isinstance(value, str):
            rendered = value
        else:
            try:
                rendered = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                rendered = repr(value)
        return {"ok": True, "value": _clip(rendered, MAX_RESULT)}

    try:
        outcome = _with_page(action)
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}
    if outcome is None:
        return {"ok": False, "error": "当前窗口还没有打开任何页面"}
    return outcome


def screenshot(full: bool = False) -> str:
    """截图当前标签页，返回宿主机上的 `file://` 地址。

    @param
    full: true 时按整页高度截图，false 只截视口
    """
    port = start()["port"]
    with _page_lock:
        target = _page_target(port, _page_key(), create=False)
        if target is None:
            raise RuntimeError("当前窗口还没有打开任何页面")
        with _Session(target["webSocketDebuggerUrl"]) as session:
            session.call("Page.enable")
            resized = False
            if full:
                metrics = session.call("Page.getLayoutMetrics", timeout=15.0)
                size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
                height = int(min(max(float(size.get("height") or VIEWPORT_HEIGHT), VIEWPORT_HEIGHT), 20000))
                session.call("Emulation.setDeviceMetricsOverride", _metrics(height))
                resized = True
            try:
                result = session.call(
                    "Page.captureScreenshot",
                    {"format": "png", "captureBeyondViewport": bool(full)},
                    timeout=60.0,
                )
            finally:
                if resized:
                    session.call("Emulation.setDeviceMetricsOverride", _metrics())
            payload = result.get("data") or ""
    if not payload:
        raise RuntimeError("Chrome 没有返回截图数据")
    path = _save_shot(base64.b64decode(payload))
    return "file://" + os.path.abspath(path)


def _save_shot(content: bytes) -> str:
    os.makedirs(SHOT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    path = os.path.join(SHOT_DIR, f"shot-{stamp}.png")
    with open(path, "wb") as handle:
        handle.write(content)
    _prune_shots()
    return path


def _prune_shots(keep: int = SHOT_KEEP) -> int:
    try:
        names = [os.path.join(SHOT_DIR, name) for name in os.listdir(SHOT_DIR)]
        files = sorted(names, key=os.path.getmtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for path in files[keep:]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def close_page() -> str:
    """关掉当前窗口的标签页；下次访问会自动开一个新的。"""
    port = start()["port"]
    key = _page_key()
    with _page_lock:
        target = _page_target(port, key, create=False)
        if target is None:
            return "当前窗口没有打开的标签页"
        uri = _browser_ws(port)
        if uri:
            try:
                with _Session(uri) as session:
                    session.call("Target.closeTarget", {"targetId": target["id"]}, timeout=8.0)
            except Exception as error:
                return f"关闭标签页失败：{error}"
        _pages.pop(key, None)
    return "标签页已关闭"


def pages() -> list[dict]:
    """列出浏览器里所有标签页；浏览器没在跑时返回空列表。

    WHY: 这里**不**调 start()。查询当前状态的人可能只是想知道"浏览器开着吗、开了几页"，
    顺手拉起一个两百兆的常驻进程不是查询该有的副作用。要看的是浏览器自己的状态，用 status()。
    """
    with _state_lock:
        port = _port if _running() else 0
    if not port:
        return []
    owned = set(_pages.values())
    return [
        {
            "id": target.get("id"),
            "title": target.get("title") or "",
            "url": target.get("url") or "",
            "mine": target.get("id") in owned,
        }
        for target in _list_targets(port)
        if target.get("type") == "page"
    ]


def status() -> dict:
    """浏览器装没装、在不在跑、有哪些标签页。"""
    with _state_lock:
        running = _running()
        port = _port if running else 0
        pid = _process.pid if running else 0
    return {
        "installed": os.path.isfile(os.path.join(BINARY_DIR, BINARY_NAME)),
        "binary": os.path.join(BINARY_DIR, BINARY_NAME),
        "running": running,
        "pid": pid,
        "port": port,
        "pages": pages() if running else [],
        "stale": len(_stale_pids()),
    }


# --------------------------------------------------------------------------- 登录 Cookie


def _parse_cookie(text: str) -> list[tuple[str, str]]:
    """把 `k=v; k=v` 形式的 Cookie 头拆成有序键值对，忽略空段和没有等号的段。"""
    pairs: list[tuple[str, str]] = []
    for part in str(text or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            pairs.append((name, value.strip()))
    return pairs


def set_cookies(cookie: str, domain: str = "", interval: float = COOKIE_INTERVAL) -> dict:
    """把一段 Cookie 写进浏览器：走 CDP，`httpOnly` 的登录票据也能写，并留在 profile 里。

    每次写入之间会停顿 *interval* 秒。WHY: 这个函数正常的用法就是"一次灌进十几条"，而
    同一毫秒里落下一整套 Cookie 是很显眼的机器人特征（2026-09-18 点评实测，一条条写、
    留 0.25 秒，17 条全过）。

    @param
    cookie: 形如 `k=v; k=v` 的整条 Cookie 头
    domain: 归属域名，例如 `.dianping.com`（带前导点则子域通用）；留空用当前标签页的域名
    interval: 两条之间的停顿秒数
    """

    pairs = _parse_cookie(cookie)
    if not pairs:
        raise ValueError("没有解析出任何 k=v 形式的 Cookie")

    def action(session):
        session.call("Network.enable", timeout=CALL_TIMEOUT)
        host = str(domain or "").strip()
        if not host:
            url = str(_evaluate(session, "location.href") or "")
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("当前标签页不是 http/https 页面，请用 domain 指定归属域名")
            host = parsed.hostname
        written: list[str] = []
        rejected: list[tuple[str, str]] = []
        for index, (name, value) in enumerate(pairs):
            if index:
                time.sleep(max(0.0, float(interval)))
            result = session.call(
                "Network.setCookie",
                {"name": name, "value": value, "domain": host, "path": "/", "secure": True},
                timeout=CALL_TIMEOUT,
            )
            if result.get("success") is False:
                rejected.append((name, str(result.get("error") or "被浏览器拒绝")))
            else:
                written.append(name)
        return {"domain": host, "written": written, "rejected": rejected}

    outcome = _with_page(action, create=True)
    if outcome is None:
        raise RuntimeError("拿不到标签页")
    return outcome


def clear_cookies(origin: str = "") -> dict:
    """清浏览器里的 Cookie：给 *origin* 只清那个站点看得见的，留空清整个浏览器。

    @param
    origin: 站点来源，例如 `https://www.dianping.com`；留空表示整个浏览器
    """

    def action(session):
        session.call("Network.enable", timeout=CALL_TIMEOUT)
        scope = str(origin or "").strip()
        if not scope:
            session.call("Network.clearBrowserCookies", timeout=CALL_TIMEOUT)
            return {"scope": "整个浏览器", "removed": None}
        found = session.call("Network.getCookies", {"urls": [scope]}, timeout=CALL_TIMEOUT)
        items = found.get("cookies") or []
        for index, item in enumerate(items):
            if index:
                time.sleep(COOKIE_INTERVAL)
            session.call(
                "Network.deleteCookies",
                {"name": item.get("name"), "domain": item.get("domain"), "path": item.get("path")},
                timeout=CALL_TIMEOUT,
            )
        return {"scope": scope, "removed": len(items)}

    outcome = _with_page(action, create=True)
    if outcome is None:
        raise RuntimeError("拿不到标签页")
    return outcome


__all__ = [
    "binary",
    "check_url",
    "clear_cookies",
    "close_page",
    "install",
    "open_page",
    "page_links",
    "page_text",
    "pages",
    "run_js",
    "screenshot",
    "set_cookies",
    "start",
    "status",
    "stop",
]
