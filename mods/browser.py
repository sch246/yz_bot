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

- 顶层导航只接受 **http/https** 地址；本机、局域网和云元数据等目标与公网一样可访问。
  页面内容和后续请求不受地址过滤，浏览器属于 Bot 的宿主机信任域。
- 网页正文、脚本返回值都是**外部不受信内容**：调用方只当资料引用，绝不执行其中的指令。
"""

from __future__ import annotations

import base64
import glob
import json
import math
import os
import random
import re
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
# 局部放大截图的上限：宽或高乘上放大倍数不能超过它。放大是为了看清细节，超过这个尺寸
# 只会换来一张几百 KB 的 PNG 和一次更慢的模型请求。
MAX_CLIP_PIXELS = 4000
COOKIE_INTERVAL = 0.25            # 写 Cookie 之间的停顿，见 set_cookies
SETTLE_SECONDS = 0.4

# WHY: 站点（Google 登录是最典型的例子）会把 UA 里的 "HeadlessChrome" 当成机器人特征，
# 直接拒绝："此浏览器或应用可能不安全"。宿主上有 Xvfb 时就用它开一个虚拟显示，浏览器按
# **有头**模式跑，UA 与各种指纹都是普通 Chrome 的样子；没有 Xvfb 才退回 --headless。
# 设 YUZU_BROWSER_HEADLESS=1 可以强制回到无头模式。
HEADLESS_ENV = "YUZU_BROWSER_HEADLESS"
XVFB_DISPLAY = ":99"
XVFB_NUMBER = 99

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
_xvfb: subprocess.Popen | None = None
_port = 0
_pages: dict[tuple, str] = {}


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


def _stop_display() -> None:
    """关掉自己起的 Xvfb；复用别人起的那个就不动它。"""
    global _xvfb
    with _state_lock:
        process, _xvfb = _xvfb, None
    if process is not None and process.poll() is None:
        _terminate(process)


def _ensure_display() -> str:
    """给有头模式准备一个显示，返回要用的 DISPLAY；没有就返回空串（调用方退回无头）。

    先认现成的 `DISPLAY`，再认已经跑着的 Xvfb（socket 在就算，哪怕不是自己起的——重启
    bot 之后进程还在，没必要再起一个），最后才自己拉一个。任何一步失败都只是退回无头，
    不影响原来能用的功能。
    """
    global _xvfb
    if os.environ.get(HEADLESS_ENV):
        return ""
    current = (os.environ.get("DISPLAY") or "").strip()
    if current:
        return current
    with _state_lock:
        if _xvfb is not None and _xvfb.poll() is None:
            os.environ["DISPLAY"] = XVFB_DISPLAY
            return XVFB_DISPLAY
        socket_path = f"/tmp/.X11-unix/X{XVFB_NUMBER}"
        if not os.path.exists(socket_path):
            executable = shutil.which("Xvfb")
            if not executable:
                return ""
            os.makedirs(ROOT, exist_ok=True)
            stream = open(LOG_PATH, "ab", buffering=0)
            with watchdog.detached():
                _xvfb = subprocess.Popen(
                    [
                        executable,
                        XVFB_DISPLAY,
                        "-screen",
                        "0",
                        f"{VIEWPORT_WIDTH}x{VIEWPORT_HEIGHT}x24",
                        "-nolisten",
                        "tcp",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    cwd=ROOT,
                )
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if os.path.exists(socket_path):
                    break
                if _xvfb.poll() is not None:
                    _stream.info("Xvfb 起不来，退回无头模式", exc_info=False)
                    _xvfb = None
                    return ""
                time.sleep(0.2)
            if not os.path.exists(socket_path):
                _stream.info("Xvfb 十秒内没就绪，退回无头模式")
                return ""
        os.environ["DISPLAY"] = XVFB_DISPLAY
        return XVFB_DISPLAY


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
    display = _ensure_display()
    command = [
        executable,
        *([] if display else ["--headless"]),
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
    _stop_display()


# --------------------------------------------------------------------------- 地址检查


def check_url(url: str) -> str:
    """规范化顶层导航地址；只检查 http/https 语法，不解析或限制目标主机。

    WHY: 浏览器与代码、shell 工具同属 Bot 的宿主机信任域，本机和内网 HTTP 服务是明确
    可用的目标；地址隔离不属于这层工具的职责。

    @param
    url: 完整地址；没写协议时按 https 补
    """
    candidate = (url or "").strip()
    if not candidate:
        raise ValueError("URL 不能为空")
    if "://" not in candidate:
        prefix, separator, remainder = candidate.partition(":")
        if (separator and re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", prefix)
                and not re.match(r"\d+(?:[/?#]|$)", remainder)):
            raise ValueError(f"只允许 http/https 地址，收到 {prefix.lower()}")
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"只允许 http/https 地址，收到 {scheme or '空协议'}")
    host = parsed.hostname
    if not host:
        raise ValueError("地址里没有主机名")
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
        # WHY: 加载完成事件可能在 Page.navigate 的回应前到达，必须在等待任何命令时
        # 都送给同一处理器，不能只在 wait_until 中接收。
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
            message = self._receive(deadline)
            if "id" in message:
                if message.get("id") == ident:
                    return self._settle(message, ident)
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


# --------------------------------------------------------------------------- 动作


_JS_STATE = (
    "(() => ({"
    "title: document.title,"
    "url: location.href,"
    "ready: document.readyState,"
    "chars: document.body && document.body.innerText ? document.body.innerText.length : 0"
    "}))()"
)

_JS_BOX = (
    "(() => {"
    "const el = document.querySelector(__SELECTOR__);"
    "if (!el) return null;"
    "const rect = el.getBoundingClientRect();"
    "return {"
    "x: rect.x + window.scrollX,"
    "y: rect.y + window.scrollY,"
    "width: rect.width,"
    "height: rect.height"
    "};"
    "})()"
)

_JS_SCROLL = "[window.scrollX, window.scrollY]"

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
        loaded = threading.Event()
        with _Session(target["webSocketDebuggerUrl"]) as session:
            session.handler = lambda message: loaded.set() if message.get("method") == "Page.loadEventFired" else None
            session.call("Page.enable")
            session.call("Emulation.setDeviceMetricsOverride", _metrics())
            session.call("Page.navigate", {"url": target_url})
            arrived = session.wait_until(loaded.is_set, timeout)
            session.handler = None
            if arrived:
                # WHY: load 之后还有一批 SPA 会异步把首屏渲染出来，等一下再读，拿到的
                # 正文通常从"空壳"变成"有内容"。
                time.sleep(SETTLE_SECONDS)
            state = _state(session)
    state["loaded"] = arrived
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


def _region_clip(session: _Session, region: str, scale: float = 0.0) -> dict | None:
    """把 *region* 的说法翻成 CDP 的 clip；留空返回 None（表示整屏）。

    WHY: 数字坐标一律按**视口**算，再自己加上滚动偏移。模型看图看的就是视口那一张，报出来
    的坐标自然是视口坐标；照搬给 CDP 会错在已经滚动过的页面上，而那种错看起来像"截错了
    地方"，不像坐标系不一致。

    @param
    region: `css:选择器` 或 `x,y,w,h`（视口内 CSS 像素）
    scale: 放大倍数，0 表示默认 2 倍
    """
    text = str(region or "").strip()
    if not text:
        return None
    zoom = float(scale) if scale and scale > 0 else 2.0
    if text.lower().startswith("css:"):
        selector = text[4:].strip()
        if not selector:
            raise ValueError("css: 后面要给一个选择器，例如 css:#captcha")
        box = _evaluate(session, _JS_BOX.replace("__SELECTOR__", json.dumps(selector)))
        if not isinstance(box, dict) or not box.get("width") or not box.get("height"):
            raise ValueError(f"选择器没有匹配到有尺寸的元素：{selector}")
        x, y = float(box["x"]), float(box["y"])
        width, height = float(box["width"]), float(box["height"])
    else:
        numbers = [part.strip() for part in text.replace("，", ",").split(",")]
        if len(numbers) != 4:
            raise ValueError("region 要写成 x,y,w,h（视口内的 CSS 像素）或 css:选择器")
        try:
            x, y, width, height = (float(value) for value in numbers)
        except ValueError as error:
            raise ValueError("region 的四个数字没读懂，写成 x,y,w,h") from error
        offset = _evaluate(session, _JS_SCROLL) or [0, 0]
        x += float(offset[0] or 0)
        y += float(offset[1] or 0)
    if width <= 0 or height <= 0:
        raise ValueError("region 的宽高要大于 0")
    zoom = min(zoom, MAX_CLIP_PIXELS / width, MAX_CLIP_PIXELS / height)
    if zoom <= 0.05:
        raise ValueError("region 太小了（不足 5% 原始大小），换个范围再试")
    return {
        "x": round(x, 2),
        "y": round(y, 2),
        "width": round(width, 2),
        "height": round(height, 2),
        "scale": round(zoom, 3),
    }



# ------------------------------------------------------------------ 真鼠标、真键盘
#
# WHY: 页面里的 `el.click()` 是**合成事件**——没有真实指针轨迹，`isTrusted` 为假。多数站点
# 不在乎，但账号选择器、滑块、人机验证这类地方专门盯着它：要么没反应，要么直接判你是脚本。
# 所以这里走 CDP 的 `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent`，让浏览器自己产生
# 带 `isTrusted` 的输入事件。坐标一律是**视口内 CSS 像素**（鼠标事件就这么算），元素类落点
# 用 `getBoundingClientRect()` 现场换算，滚动过的页面也不会错位。

_JS_CLICK_POINT = (
    "(() => {"
    "const el = document.querySelector(__SELECTOR__);"
    "if (!el) return null;"
    "el.scrollIntoView({block: 'center', inline: 'center'});"
    "const rect = el.getBoundingClientRect();"
    "const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;"
    "const hit = document.elementFromPoint(x, y);"
    "return {"
    "x: x, y: y, width: rect.width, height: rect.height,"
    "tag: el.tagName.toLowerCase(),"
    "label: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('name') || '').trim().replace(/\\s+/g, ' ').slice(0, 60),"
    "covered: (hit && hit !== el && !el.contains(hit) && !hit.contains(el))"
    " ? (hit.tagName.toLowerCase() + (hit.className ? '.' + String(hit.className).split(' ')[0] : '')) : ''"
    "};})()"
)

_JS_MOUSE = "[window.__yuzuMouseX || 0, window.__yuzuMouseY || 0]"

#: 常用按键：名称 -> (key, code, windowsVirtualKeyCode, 要发的字符)
KEYS = {
    "enter": ("Enter", "Enter", 13, "\r"),
    "tab": ("Tab", "Tab", 9, ""),
    "escape": ("Escape", "Escape", 27, ""),
    "backspace": ("Backspace", "Backspace", 8, ""),
    "delete": ("Delete", "Delete", 46, ""),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, ""),
    "arrowright": ("ArrowRight", "ArrowRight", 39, ""),
    "arrowup": ("ArrowUp", "ArrowUp", 38, ""),
    "arrowdown": ("ArrowDown", "ArrowDown", 40, ""),
    "pageup": ("PageUp", "PageUp", 33, ""),
    "pagedown": ("PageDown", "PageDown", 34, ""),
    "home": ("Home", "Home", 36, ""),
    "end": ("End", "End", 35, ""),
    "space": (" ", "Space", 32, " "),
}

_BUTTON_BITS = {"left": 1, "right": 2, "middle": 4}


def _point(session: _Session, target: str) -> dict:
    """把 `css:选择器` 或 `x,y` 翻成一个视口坐标点（含元素信息）。

    @param
    session: 当前标签页的 CDP 连接
    target: 落点说法
    """
    text = str(target or "").strip()
    if not text:
        raise ValueError("要给个落点：`css:选择器` 或 `x,y`（视口坐标）")
    if text.lower().startswith("css:"):
        selector = text[4:].strip()
        if not selector:
            raise ValueError("css: 后面要给一个选择器，例如 css:#captcha")
        box = _evaluate(session, _JS_CLICK_POINT.replace("__SELECTOR__", json.dumps(selector)))
        if not isinstance(box, dict):
            raise ValueError(f"选择器没匹配到元素：{selector}（跨 iframe 的元素用 x,y 坐标来点）")
        if not box.get("width") or not box.get("height"):
            raise ValueError(f"元素没有尺寸，点不着：{selector}")
        return {**box, "how": f"css:{selector}"}
    numbers = [part.strip() for part in text.replace("，", ",").split(",")]
    if len(numbers) != 2:
        raise ValueError("落点要写成 `x,y`（视口坐标）或 `css:选择器`")
    try:
        x, y = (float(value) for value in numbers)
    except ValueError as error:
        raise ValueError("x,y 没读懂：两个数字，逗号隔开") from error
    return {"x": x, "y": y, "width": 0, "height": 0, "tag": "", "label": "", "covered": "", "how": text}


def _viewport(session: _Session) -> tuple:
    size = _evaluate(session, "[window.innerWidth, window.innerHeight]") or [1280, 900]
    return float(size[0] or 1280), float(size[1] or 900)


def _path(x0: float, y0: float, x1: float, y1: float, steps: int, human: bool) -> list:
    """从 (x0,y0) 走到 (x1,y1) 的一串路径点，最后一点精确落在终点。

    WHY: 一次 `mouseMoved` 直接跳到目标，轨迹在页面上就是"瞬移"，滑块和风控都把这当机器人。
    这里按 smoothstep 缓入缓出切成若干小步，再叠一点垂直于路径的抖动。

    @param
    x0: 起点 x
    y0: 起点 y
    x1: 终点 x
    y1: 终点 y
    steps: 切几段，至少 1
    human: 是否加缓动与抖动
    """
    count = max(1, int(steps))
    dx, dy = x1 - x0, y1 - y0
    distance = math.hypot(dx, dy)
    points: list = []
    for index in range(1, count + 1):
        t = index / count
        eased = t * t * (3 - 2 * t) if human else t
        px, py = x0 + dx * eased, y0 + dy * eased
        if human and distance > 6:
            normal_x, normal_y = -dy / distance, dx / distance
            amplitude = math.sin(math.pi * t) * min(2.5, distance * 0.02) * random.uniform(-1, 1)
            px += normal_x * amplitude
            py += normal_y * amplitude
        points.append((round(px, 1), round(py, 1)))
    points[-1] = (round(x1, 1), round(y1, 1))
    return points


def _move_pointer(session: _Session, x: float, y: float, human: bool, buttons: int = 0, span: float = 0.0) -> int:
    """把指针挪到 (x,y)，返回走了多少步；*buttons* 非 0 表示"按住移动"（拖拽）。

    @param
    session: 当前标签页的 CDP 连接
    x: 目标 x（视口坐标）
    y: 目标 y（视口坐标）
    human: 是否缓动 + 抖动
    buttons: 按住时传 1（左键拖拽）
    span: 整段路程大约用多少秒，0 表示按默认节奏
    """
    here = _evaluate(session, _JS_MOUSE) or [0, 0]
    hx, hy = float(here[0] or 0), float(here[1] or 0)
    distance = math.hypot(x - hx, y - hy)
    if human:
        steps = max(8, min(48, int(distance / 14) + 8))
    else:
        steps = 1
    points = _path(hx, hy, x, y, steps, human)
    if span > 0:
        gap = span / len(points)
    elif human:
        gap = 0.012
    else:
        gap = 0.004
    for px, py in points:
        session.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": px, "y": py, "button": "none" if not buttons else "left", "buttons": buttons},
        )
        time.sleep(gap * random.uniform(0.6, 1.4))
    _evaluate(session, f"[window.__yuzuMouseX = {json.dumps(round(x, 1))}, window.__yuzuMouseY = {json.dumps(round(y, 1))}]")
    return len(points)


def _press_key(session: _Session, name: str) -> None:
    """按一下具名按键（Enter/Tab/Backspace…），期间发完整的 down/char/up。

    @param
    session: 当前标签页的 CDP 连接
    name: KEYS 里的键名，大小写不敏感
    """
    key = KEYS.get(str(name or "").strip().lower())
    if key is None:
        raise ValueError(f"不认识的按键：{name}；可用的是 {', '.join(sorted(KEYS))}")
    label, code, virtual, text = key
    session.call(
        "Input.dispatchKeyEvent",
        {"type": "rawKeyDown", "key": label, "code": code,
         "windowsVirtualKeyCode": virtual, "nativeVirtualKeyCode": virtual},
    )
    if text:
        session.call("Input.dispatchKeyEvent", {"type": "char", "key": label, "text": text, "unmodifiedText": text})
    session.call(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": label, "code": code,
         "windowsVirtualKeyCode": virtual, "nativeVirtualKeyCode": virtual},
    )
    time.sleep(random.uniform(0.03, 0.08))


def click(target: str = "", human: bool = True, count: int = 1, button: str = "left", hold: float = 0.0) -> dict:
    """在页面上真的点一下鼠标（CDP 真实指针事件，不是 `el.click()`）。

    @param
    target: 落点：`css:选择器` 点元素中心（会自动滚进视野），或 `x,y`（视口内 CSS 像素）
    human: true 时把移动拆成小步并带轻微抖动（更像人），false 一步到位
    count: 连点几次，2 就是双击
    button: left / right / middle
    hold: 按下后停多少秒再松开；0 表示用 0.05~0.12 秒的随机停顿
    """
    if button not in _BUTTON_BITS:
        raise ValueError(f"button 只能是 {', '.join(_BUTTON_BITS)}")
    port = start()["port"]
    with _page_lock:
        tab = _page_target(port, _page_key())
        if tab is None:
            raise RuntimeError("当前窗口还没有打开任何页面")
        with _Session(tab["webSocketDebuggerUrl"]) as session:
            point = _point(session, target)
            x, y = float(point["x"]), float(point["y"])
            width, height = _viewport(session)
            if not (0 <= x <= width and 0 <= y <= height):
                raise ValueError(
                    f"落点 ({x:g},{y:g}) 不在视口里（视口 {width:g}x{height:g}）；换个 css: 选择器让它滚进来"
                )
            moved = _move_pointer(session, x, y, human)
            bits = _BUTTON_BITS[button]
            for index in range(1, int(count) + 1):
                session.call(
                    "Input.dispatchMouseEvent",
                    {"type": "mousePressed", "x": x, "y": y, "button": button,
                     "buttons": bits, "clickCount": index},
                )
                time.sleep(hold if hold > 0 else random.uniform(0.05, 0.12))
                session.call(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseReleased", "x": x, "y": y, "button": button,
                     "buttons": 0, "clickCount": index},
                )
                time.sleep(random.uniform(0.05, 0.12))
            return {"ok": True, "x": round(x, 1), "y": round(y, 1), "count": int(count), "button": button,
                    "steps": moved, "tag": point.get("tag", ""), "label": point.get("label", ""),
                    "covered": point.get("covered", ""), "how": point["how"],
                    "viewport": [width, height]}


def drag(source: str, target: str, human: bool = True, duration: float = 0.0, hold: float = 0.0) -> dict:
    """按住 *source* 拖到 *target* 再松开——滑块类验证码要的就是这个。

    @param
    source: 起点，`css:选择器` 或 `x,y`（视口坐标）
    target: 终点，写法同上
    human: true 时缓动并带轻微抖动
    duration: 整段拖拽大约花多少秒；0 表示随机 0.5~0.9 秒
    hold: 在起点按住后停多少秒再开始移动；0 表示随机 0.08~0.18 秒
    """
    port = start()["port"]
    with _page_lock:
        tab = _page_target(port, _page_key())
        if tab is None:
            raise RuntimeError("当前窗口还没有打开任何页面")
        with _Session(tab["webSocketDebuggerUrl"]) as session:
            start_point = _point(session, source)
            end_point = _point(session, target)
            sx, sy = float(start_point["x"]), float(start_point["y"])
            ex, ey = float(end_point["x"]), float(end_point["y"])
            _move_pointer(session, sx, sy, human)
            session.call("Input.dispatchMouseEvent",
                         {"type": "mousePressed", "x": sx, "y": sy, "button": "left", "buttons": 1, "clickCount": 1})
            time.sleep(hold if hold > 0 else random.uniform(0.08, 0.18))
            span = duration if duration > 0 else random.uniform(0.5, 0.9)
            _move_pointer(session, ex, ey, human, buttons=1, span=span)
            time.sleep(random.uniform(0.12, 0.3))
            session.call("Input.dispatchMouseEvent",
                         {"type": "mouseReleased", "x": ex, "y": ey, "button": "left", "buttons": 0, "clickCount": 1})
            return {"ok": True, "from": [round(sx, 1), round(sy, 1)], "to": [round(ex, 1), round(ey, 1)],
                    "seconds": round(span, 2), "how": f"{start_point['how']} -> {end_point['how']}"}


def type_text(text: str, target: str = "", clear: bool = False, slow: bool = False, submit: bool = False) -> dict:
    """往页面上打字：可先点一下取得焦点，再送入内容。

    @param
    text: 要输入的内容
    target: 先点哪里取得焦点（`css:选择器` 或 `x,y`），留空就打给当前焦点
    clear: 先 Ctrl+A 全选再删掉原有内容
    slow: true 时逐字符发真实按键（个别站点只认这个）；false 用一次 insertText，快且中文稳
    submit: 打完按一次回车
    """
    port = start()["port"]
    with _page_lock:
        tab = _page_target(port, _page_key())
        if tab is None:
            raise RuntimeError("当前窗口还没有打开任何页面")
        with _Session(tab["webSocketDebuggerUrl"]) as session:
            focused = ""
            if str(target or "").strip():
                point = _point(session, target)
                x, y = float(point["x"]), float(point["y"])
                width, height = _viewport(session)
                if not (0 <= x <= width and 0 <= y <= height):
                    raise ValueError(f"落点 ({x:g},{y:g}) 不在视口里，先让它滚进来再点")
                _move_pointer(session, x, y, True)
                session.call("Input.dispatchMouseEvent",
                             {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1})
                time.sleep(random.uniform(0.04, 0.1))
                session.call("Input.dispatchMouseEvent",
                             {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1})
                time.sleep(random.uniform(0.08, 0.16))
                focused = point.get("how", "")
            if clear:
                session.call("Input.dispatchKeyEvent",
                             {"type": "rawKeyDown", "key": "a", "code": "KeyA", "modifiers": 2,
                              "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65})
                session.call("Input.dispatchKeyEvent",
                             {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2,
                              "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65})
                time.sleep(random.uniform(0.05, 0.12))
                _press_key(session, "backspace")
            payload = str(text if text is not None else "")
            if slow:
                for char in payload:
                    if char == "\n":
                        _press_key(session, "enter")
                        continue
                    session.call("Input.dispatchKeyEvent",
                                 {"type": "keyDown", "text": char, "unmodifiedText": char, "key": char})
                    session.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": char})
                    time.sleep(random.uniform(0.04, 0.12))
            elif payload:
                session.call("Input.insertText", {"text": payload})
                time.sleep(random.uniform(0.06, 0.15))
            if submit:
                _press_key(session, "enter")
            state = _evaluate(session,
                              "(() => { const el = document.activeElement;"
                              " if (!el) return null;"
                              " return {tag: el.tagName.toLowerCase(),"
                              " type: (el.getAttribute('type') || ''),"
                              " length: (typeof el.value === 'string' ? el.value.length : -1)}; })()")
            return {"ok": True, "focused": focused, "typed": len(payload), "slow": bool(slow),
                    "submit": bool(submit), "active": state or {}}


def press_key(name: str) -> dict:
    """按一下具名按键：enter / tab / escape / backspace / delete / arrowleft / pagedown / home / end / space。

    @param
    name: 按键名，大小写不敏感
    """
    port = start()["port"]
    with _page_lock:
        tab = _page_target(port, _page_key())
        if tab is None:
            raise RuntimeError("当前窗口还没有打开任何页面")
        with _Session(tab["webSocketDebuggerUrl"]) as session:
            _press_key(session, name)
            return {"ok": True, "key": str(name).strip().lower()}


def scroll(amount: int = 0, target: str = "", x: float = 0, y: float = 0,
           steps: int = 0) -> dict:
    """滚动页面：滚轮式滚动 *amount* 像素（负数向上），或把某个元素滚进视野。

    @param
    amount: 纵向滚动像素，正数向下；0 表示不动（配合 target 用）
    target: `css:选择器`，把它滚到视野中央
    x: 滚轮落点横坐标（视口内 CSS 像素）；0 表示页面水平中央
    y: 滚轮落点纵坐标；0 表示页面垂直中央。列表/下拉这种**自己内部可滚**的容器，
       把光标放进容器里滚才有效——这就是它和滚整页的区别
    steps: 把 amount 拆成几次滚（默认 1 次一口；给 4、6 这种更像人手）
    """
    port = start()["port"]
    with _page_lock:
        tab = _page_target(port, _page_key())
        if tab is None:
            raise RuntimeError("当前窗口还没有打开任何页面")
        with _Session(tab["webSocketDebuggerUrl"]) as session:
            if str(target or "").strip():
                text = str(target).strip()
                if not text.lower().startswith("css:"):
                    raise ValueError("target 要写成 css:选择器")
                selector = text[4:].strip()
                box = _evaluate(session, _JS_CLICK_POINT.replace("__SELECTOR__", json.dumps(selector)))
                if not isinstance(box, dict):
                    raise ValueError(f"选择器没匹配到元素：{selector}")
                return {"ok": True, "scrolled_to": selector, "center": [round(box["x"], 1), round(box["y"], 1)]}
            if int(amount) == 0:
                raise ValueError("给个 amount（正数向下滚）或 target（css:选择器）")
            width, height = _viewport(session)
            point_x = float(x) if float(x) > 0 else width / 2
            point_y = float(y) if float(y) > 0 else height / 2
            total = int(amount)
            parts = max(1, min(12, int(steps) or 1))
            done = 0
            for index in range(parts):
                piece = total // parts
                if index < total % parts:
                    piece += 1 if total > 0 else -1
                if piece == 0:
                    continue
                done += piece
                session.call("Input.dispatchMouseEvent",
                             {"type": "mouseWheel", "x": point_x, "y": point_y,
                              "deltaX": 0, "deltaY": piece})
                time.sleep(random.uniform(0.03, 0.09))
            time.sleep(random.uniform(0.08, 0.2))
            offset = _evaluate(session, _JS_SCROLL) or [0, 0]
            return {"ok": True, "amount": done, "point": [round(point_x, 1), round(point_y, 1)],
                    "steps": parts, "scroll": [offset[0], offset[1]]}


def screenshot(full: bool = False, region: str = "", scale: float = 0.0) -> str:
    """截图当前标签页，返回宿主机上的 `file://` 地址。

    @param
    full: true 时按整页高度截图，false 只截视口
    region: 只截其中一块并放大，用来看细节：`css:选择器`（如 `css:#captcha`）或
            `x,y,w,h`（视口内 CSS 像素）；留空截整屏
    scale: region 的放大倍数，默认 2；1 表示按原始像素
    """
    port = start()["port"]
    with _page_lock:
        target = _page_target(port, _page_key(), create=False)
        if target is None:
            raise RuntimeError("当前窗口还没有打开任何页面")
        with _Session(target["webSocketDebuggerUrl"]) as session:
            session.call("Page.enable")
            clip = _region_clip(session, region, scale)
            resized = False
            if full and clip is None:
                metrics = session.call("Page.getLayoutMetrics", timeout=15.0)
                size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
                height = int(min(max(float(size.get("height") or VIEWPORT_HEIGHT), VIEWPORT_HEIGHT), 20000))
                session.call("Emulation.setDeviceMetricsOverride", _metrics(height))
                resized = True
            try:
                params = {"format": "png", "captureBeyondViewport": bool(full or clip)}
                if clip is not None:
                    params["clip"] = clip
                result = session.call("Page.captureScreenshot", params, timeout=60.0)
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
    "click",
    "drag",
    "press_key",
    "scroll",
    "type_text",
]
